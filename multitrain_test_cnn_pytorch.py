from __future__ import print_function
import os, sys, time, argparse
import h5py
import numpy as np
from collections import defaultdict
from hdf5_deeplearn_utils import MultiHDF5VisualIterator, MultiHDF5EncoderDecoderVisualIterator
import torch
import torchvision
import torchvision.transforms as transforms
import Nets
import Nets_Spiking
import Nets_Spiking_BNTT
import shutil

def save_ckp(state, checkpoint_dir, comb_filename):
    f_path = os.path.join(checkpoint_dir, comb_filename + '_checkpoint.pt')
    torch.save(state, f_path)

def load_ckp(checkpoint_dir, comb_filename, model, optimizer):
    checkpoint_fpath = os.path.join(checkpoint_dir,  comb_filename + '_checkpoint.pt')
    print("Looking for: ", checkpoint_fpath)
    checkpoint = torch.load(checkpoint_fpath)
    model.load_state_dict(checkpoint['state_dict'])
    optimizer.load_state_dict(checkpoint['optimizer'])
    return model, optimizer, checkpoint['epoch'], checkpoint['test_error']

def evaluate_checkpoint(checkpoint_dir, comb_filename):
    checkpoint_fpath = os.path.join(checkpoint_dir,  comb_filename + '_checkpoint.pt')
    checkpoint = torch.load(checkpoint_fpath)
    print("Checkpoint Found:", checkpoint_fpath)
    print("Epochs:", checkpoint["epoch"])
    print("Train Error:", checkpoint["train_error"])
    print("Test Error:", checkpoint["test_error"])    

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Train a driving network.')
    # File and path naming stuff
    parser.add_argument('--h5files',    nargs='+', default='/home/dneil/h5fs/driving/rec1487864316_bin5k.hdf5', help='HDF5 File that has the data.')
    parser.add_argument('--run_id',       default='default', help='ID of the run, used in saving.')
    parser.add_argument('--checkpoint_dir',       default=None, help='Checkpoint file if we are resuming the training')
    parser.add_argument('--filename',     default='driving_cnn_19.4_multi', help='Filename to save model and log to.')
    parser.add_argument('--result_dir',     default='saved_models', help='Folder to save the model')
    parser.add_argument('--optimizer',       default='Adam', help='Optimizer to use. Adam or  SGD')
    parser.add_argument('--lr',       default=0.1, type=float, help='Learning Rate')
    parser.add_argument('--img_size',         default=80, type=int, help='Dimension of image. Assumed to be square')
    # Control meta parameters
    parser.add_argument('--seed',         default=42, type=int, help='Initialize the random seed of the run (for reproducibility).')
    parser.add_argument('--batch_size',   default=16, type=int, help='Batch size.')
    parser.add_argument('--num_epochs',   default=30, type=int, help='Number of epochs to train for.')
    parser.add_argument('--patience',     default=4, type=int, help='How long to wait for an increase in validation error before quitting.')
    parser.add_argument('--patience_key', default='test_acc', help='What key to look at before quitting.')
    parser.add_argument('--wait_period',  default=10, type=int, help='How long to wait before looking for early stopping.')
    parser.add_argument('--dataset_keys',  nargs='+', default='aps_frame_48x64', help='Which dataset key (APS, DVS, etc.) to use.')
    parser.add_argument('--evaluate', action='store_true', help='Boolean variable to evaluate saved model.')
    parser.add_argument('--evaluate_ckp', action='store_true', help='Boolean variable to evaluate checkpoint.')
    parser.add_argument('--calc_activity', action='store_true', help='Boolean variable to calculate activity.')
    # --- DVS Dataset related args --- #
    parser.add_argument('--dvs', action='store_true', help='Boolean value if we are using DVS data')
    parser.add_argument('--seperate_dvs_channels', action='store_true', help='DVS frames with seperated polarity channesl')
    parser.add_argument('--split_timesteps', action='store_true', help='Split DVS frame across time dimension')
    parser.add_argument('--timesteps', type=int, default=10, help='Number of timesteps to split')
    # --- SNN related args --- #
    parser.add_argument('--snn', action='store_true', help='Flag to run an SNN')
    parser.add_argument('--activation', default='Linear', type=str, help='SNN activation function', choices=['Linear', 'STDB'])
    parser.add_argument('--alpha', default=0.3, type=float, help='parameter alpha for STDB')
    parser.add_argument('--beta', default=0.01, type=float, help='parameter beta for STDB')
    parser.add_argument('--snn_kernel_size', default=3, type=int, help='filter size for the conv layers')
    parser.add_argument('--leak', default=1.0, type=float, help='membrane leak')
    parser.add_argument('--scaling_factor', default=0.7, type=float, help='scaling factor for thresholds at reduced timesteps')
    parser.add_argument('--default_threshold', default=1.0, type=float, help='intial threshold to train SNN from scratch')
    parser.add_argument('--dropout', default=0.3, type=float, help='dropout percentage for conv layers')
    parser.add_argument('--BNTT', action='store_true', help='Flag to run an BNTT')
    parser.add_argument('--hybrid', action='store_true', help='Boolean variable to use hybrid SNN + ANN model.')
    # --- Encoder Decoder Architecture args --- #
    parser.add_argument('--encoder_decoder', action='store_true', help='Flag to run an encoder decoder architecture')
    parser.add_argument('--h5files_aps',    nargs='+', help='HDF5 File that has APS data.')
    parser.add_argument('--dataset_keys_aps',  nargs='+', default='aps_frame_80x80', help='Dataset key for APS.')
    parser.add_argument('--h5files_dvs',    nargs='+', help='HDF5 File that has DVS data.')
    parser.add_argument('--dataset_keys_dvs',  nargs='+', default='dvs_frame_80x80', help='Dataset key for DVS.')
    parser.add_argument('--use_encoder', action='store_true', help='Use DVS encoder')
    parser.add_argument('--pretrained_ed_model', default='./saved_models/driving_cnn_19.4_multi_encoder_decoder', help='Encoder Decoder pretrained model')
    parser.add_argument('--noise', default=0.0, type=float, help='Level of gaussian noise to be added')
    args = parser.parse_args()

    # Set seed
    np.random.seed(args.seed)

    # Set the save name
    comb_filename = '_'.join([args.filename, args.run_id, args.optimizer, str(args.lr)])

    if args.evaluate_ckp:
        evaluate_checkpoint(args.checkpoint_dir, comb_filename)
        sys.exit()
        
    # Load dataset
    if args.encoder_decoder:
        h5fs_aps_ = [h5py.File(h5file, 'r') for h5file in args.h5files_aps]
        h5fs_dvs_ = [h5py.File(h5file, 'r') for h5file in args.h5files_dvs]
        h5fs_aps = []
        h5fs_dvs = []
        # Filter out corrupted data
        for h5_a, h5_d in zip(h5fs_aps_, h5fs_dvs_):
            if ("aps_frame_80x80" in h5_a.keys()) and ("dvs_split_80x80" in h5_d.keys()):
                h5fs_aps.append(h5_a)
                h5fs_dvs.append(h5_d)
    else:
        h5fs = []
        dataset_keys = []
        h5fs_ = [h5py.File(h5file, 'r') for h5file in args.h5files]
        # Filter out corrupted data
        for i in range(len(h5fs_)):
            if args.dataset_keys[i] in h5fs_[i].keys():
                h5fs.append(h5fs_[i])
                dataset_keys.append(args.dataset_keys[i])
        args.dataset_keys = dataset_keys

    if args.seperate_dvs_channels and args.use_encoder == False:
        num_channels = 2
    else:
        num_channels = 1


    encoder_network = None
    if args.snn:
        if args.BNTT:
            if args.dvs and args.encoder_decoder:
                model_args = {'timesteps': args.timesteps,
                              'img_size': args.img_size,
                              'inp_maps': 2,
                              'num_cls': 1,
                              'inp_type': 'dvs',
                              'encoder_decoder': args.encoder_decoder}
            elif args.dvs:
                model_args = {'timesteps': args.timesteps,
                              'img_size': args.img_size,
                              'inp_maps': 2,
                              'num_cls': 1,
                              'inp_type': 'dvs'}
            else:
                model_args = {'timesteps': args.timesteps,
                              'img_size': args.img_size,
                              'inp_maps': 1,
                              'num_cls': 1,
                              'inp_type': 'aps'}
            if args.hybrid:
                network = Nets_Spiking_BNTT.HYBRID16_TBN(**model_args)
                # network = Nets_Spiking_BNTT.HYBRID_VGG5_TBN(**model_args)
                # network = Nets_Spiking_BNTT.AVSNN_TBN(**model_args)
            else:
                network = Nets_Spiking_BNTT.HYBRID16_TBN_FULL_SNN(**model_args)
                # network = Nets_Spiking_BNTT.HYBRID_VGG5_TBN_FULL_SNN(**model_args)
                # network = Nets_Spiking_BNTT.SNN_VGG9_TBN(**model_args)
        else:
            model_args = {'vgg_name': 'VGG16',
                          'activation': args.activation,
                          'labels': 1,
                          'timesteps': args.timesteps,
                          'leak': args.leak,
                          'default_threshold': args.default_threshold,
                          'alpha': args.alpha,
                          'beta': args.beta,
                          'dropout': args.dropout,
                          'kernel_size': args.snn_kernel_size,
                          'dataset': 'ddd20'}
            network = Nets_Spiking.VGG_SNN_STDB(**model_args)
    else:
        #network = Nets.VGG16(num_channels = num_channels)
        #network = Nets.ResNet34(num_channels = num_channels)
        network = Nets.HYBRID_BASELINE(num_channels = num_channels, img_size=args.img_size)
        # network = Nets.HYBRID_BASELINE_VGG5(num_channels = num_channels, img_size=args.img_size)
        # network = Nets.ANN_AV_NN(num_channels = num_channels, img_size=args.img_size)
        if args.use_encoder:
            model_args = {'timesteps': args.timesteps,
                          'img_size': args.img_size,
                          'inp_maps': 2,
                          'num_cls': 1,
                          'inp_type': 'dvs',
                          'encoder_decoder': True}
            encoder_network = Nets_Spiking_BNTT.SNN_VGG9_TBN(**model_args)
            encoder_network.load_state_dict(torch.load(args.pretrained_ed_model))
            encoder_network.eval()
    
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    network = torch.nn.DataParallel(network, device_ids=[0])
    network.to(device)
    if encoder_network:
        encoder_network.to(device)

    # Precalc for announcing
    if args.encoder_decoder:
        num_train_batches_aps = int(np.ceil(float(np.sum([len(h5f['train_idxs']) for h5f in h5fs_aps]))/args.batch_size))
        num_test_batches_aps = int(np.ceil(float(np.sum([len(h5f['test_idxs']) for h5f in h5fs_aps]))/args.batch_size))
        num_train_batches_dvs = int(np.ceil(float(np.sum([len(h5f['train_idxs']) for h5f in h5fs_dvs]))/args.batch_size))
        num_test_batches_dvs = int(np.ceil(float(np.sum([len(h5f['test_idxs']) for h5f in h5fs_dvs]))/args.batch_size))
        print(num_train_batches_aps,num_train_batches_dvs)
        # assert (num_train_batches_aps == num_train_batches_dvs)
        # assert (num_test_batches_aps == num_test_batches_dvs)
        num_train_batches = num_train_batches_aps
        num_test_batches = num_test_batches_aps
    else:
        num_train_batches = int(np.ceil(float(np.sum([len(h5f['train_idxs']) for h5f in h5fs]))/args.batch_size))
        num_test_batches = int(np.ceil(float(np.sum([len(h5f['test_idxs']) for h5f in h5fs]))/args.batch_size))
    # print(num_train_batches, num_test_batches)

    # Dump some debug data if we like
    # print(network)
    if args.encoder_decoder:
        temp = MultiHDF5EncoderDecoderVisualIterator()
        for data in temp.flow(h5fs_aps, h5fs_dvs, args.dataset_keys_aps, args.dataset_keys_dvs, 'train_idxs', batch_size=args.batch_size, shuffle=True, seperate_dvs_channels = args.seperate_dvs_channels):
            vid_aps, vid_dvs = data
            break
        # print("Input Shape: {}, Output Shape: {}".format(vid_dvs.shape, vid_aps.shape))
    else:
        temp = MultiHDF5VisualIterator()
        for data in temp.flow(h5fs, args.dataset_keys, 'train_idxs', batch_size=args.batch_size, shuffle=True, seperate_dvs_channels = args.seperate_dvs_channels):
            vid_in_, bY = data
            if args.use_encoder:
                vid_in = encoder_network(torch.from_numpy(vid_in_).to(device))
            else:
                vid_in = torch.from_numpy(vid_in_).to(device)
            break
        # print("Input Shape: {} -> {}, Output Shape: {}".format(vid_in_.shape, vid_in.shape, bY.shape))

    def adjust_learning_rate(optimizer, cur_epoch):
        # Reduce learning rate by 10 twice at epoch 30 and 60
        if cur_epoch == int(0.5*args.num_epochs) or cur_epoch == int(0.6*args.num_epochs) or cur_epoch == int(args.num_epochs*0.7) or cur_epoch == int(args.num_epochs*0.8) or cur_epoch== int(args.num_epochs*0.9):
            for param_group in optimizer.param_groups:
                 param_group['lr'] /= 5

    loss_fn = torch.nn.MSELoss()

    if args.optimizer == "Adam":
        optimizer = torch.optim.Adam(network.parameters(), lr=args.lr, weight_decay=1e-4)
    elif args.optimizer == "SGD":
       optimizer = torch.optim.SGD(network.parameters(), lr=args.lr,momentum=0.9,weight_decay=1e-4)

    if args.evaluate:
        # print("Evaluating {} on ({}, {})".format(args.run_id, h5fs, args.dataset_keys))
        test_loss = 0
        network.load_state_dict(torch.load(os.path.join(args.result_dir, comb_filename)))
        network.eval()
        num_test_batches = 0
        for data in temp.flow(h5fs, args.dataset_keys, 'test_idxs', batch_size=args.batch_size, shuffle=True, seperate_dvs_channels = args.seperate_dvs_channels):
            vid_in_, bY = data
            vid_in_ = vid_in_ + np.random.randn(*vid_in_.shape)*args.noise
            if args.use_encoder:
                vid_in = encoder_network(torch.from_numpy(vid_in_).to(device))
            else:
                vid_in = torch.from_numpy(vid_in_).float().to(device)
            if bY.shape[0] != args.batch_size:
                continue
            if args.calc_activity:
                activity = network(vid_in, calc_activity = True)
                print(activity)
            else:
                y_pred = network(vid_in)
                loss = loss_fn(y_pred, torch.from_numpy(bY).to(device))
                test_loss += loss.item()/args.batch_size
            num_test_batches += 1
            if args.calc_activity:
                break
        test_loss = np.sqrt(test_loss/num_test_batches)
        print("Test Avg RMSE: {}".format(test_loss))
        sys.exit()

    try:
        network, optimizer, start_epoch, prev_test_error = load_ckp(args.checkpoint_dir, comb_filename, network, optimizer)
        print("Found checkpoint. Resuming training ...")
    except:
        start_epoch = 0
        prev_test_error = 1e7
        print("Checkpoint not found. Training from scratch ...")

    for t in range(start_epoch, args.num_epochs):
        train_loss = 0
        if args.encoder_decoder:
            for data in temp.flow(h5fs_aps, h5fs_dvs, args.dataset_keys_aps, args.dataset_keys_dvs, 'train_idxs', batch_size=args.batch_size, shuffle=True, seperate_dvs_channels = args.seperate_dvs_channels):
                vid_aps, vid_dvs = data
                if vid_dvs.shape[0] != args.batch_size or vid_aps.shape[0] != args.batch_size:
                    continue
                vid_aps_pred = network(torch.from_numpy(vid_dvs).to(device))
                loss = loss_fn(vid_aps_pred, torch.from_numpy(vid_aps).to(device))
                train_loss += loss.item()/args.batch_size
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
            train_loss = np.sqrt(train_loss/num_train_batches)
            print("Epoch: {}, Train Avg Error: {}".format(t, train_loss))
        else:
            if args.calc_activity:
                avg_activity = [0]*7
                tot = 0
            num_train_batches = 0
            for data in temp.flow(h5fs, args.dataset_keys, 'train_idxs', batch_size=args.batch_size, shuffle=True, seperate_dvs_channels= args.seperate_dvs_channels):
                vid_in_, bY = data
                if args.use_encoder:
                    vid_in = encoder_network(torch.from_numpy(vid_in_).to(device))
                else:
                    vid_in = torch.from_numpy(vid_in_).float().to(device)
                if bY.shape[0] != args.batch_size:
                    continue
                if args.calc_activity:
                    network.eval()
                    activity = network(vid_in, calc_activity = True)
                    for i in range(4):
                        avg_activity[i] = activity[i].cpu().numpy()
                    tot += 1
                    print(activity)
                else:
                    y_pred = network(vid_in)
                    loss = loss_fn(y_pred, torch.from_numpy(bY).to(device))
                    train_loss += loss.item()/args.batch_size
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()
                num_train_batches += 1
            if args.calc_activity:
                avg_activity = [x/tot for x in avg_activity]
                print(avg_activity)
                break
            train_loss = np.sqrt(train_loss/num_train_batches)
            print("Epoch: {}, Train Avg RMSE: {}".format(t, train_loss))
        test_loss = 0
        if args.encoder_decoder:
            for data in temp.flow(h5fs_aps, h5fs_dvs, args.dataset_keys_aps, args.dataset_keys_dvs, 'test_idxs', batch_size=args.batch_size, shuffle=True, seperate_dvs_channels = args.seperate_dvs_channels):
                vid_aps, vid_dvs = data
                if vid_dvs.shape[0] != args.batch_size or vid_aps.shape[0] != args.batch_size:
                    continue
                vid_aps_pred = network(torch.from_numpy(vid_dvs).to(device))
                loss = loss_fn(vid_aps_pred, torch.from_numpy(vid_aps).to(device))
                test_loss += loss.item()/args.batch_size
            test_loss = np.sqrt(test_loss/num_test_batches)
            print("Epoch: {}, Test Avg Error: {}".format(t, test_loss))
        else:
            num_test_batches = 0
            for data in temp.flow(h5fs, args.dataset_keys, 'test_idxs', batch_size=args.batch_size, shuffle=True, seperate_dvs_channels = args.seperate_dvs_channels):
                vid_in_, bY = data
                vid_in_ = vid_in_ + np.random.randn(*vid_in_.shape)*args.noise
                if args.use_encoder:
                    vid_in = encoder_network(torch.from_numpy(vid_in_).to(device))
                else:
                    vid_in = torch.from_numpy(vid_in_).float().to(device)
                if bY.shape[0] != args.batch_size:
                    continue
                if args.calc_activity:
                    activity = network(vid_in, calc_activity = True)
                    print(activity)
                else:
                    y_pred = network(vid_in)
                    loss = loss_fn(y_pred, torch.from_numpy(bY).to(device))
                    test_loss += loss.item()/args.batch_size
                num_test_batches += 1
            if args.calc_activity:
                break
            test_loss = np.sqrt(test_loss/num_test_batches)
            print("Epoch: {}, Test Avg RMSE: {}".format(t, test_loss))
        torch.save(network.state_dict(), os.path.join(args.result_dir, comb_filename))
        checkpoint = {
            'epoch': t + 1,
            'state_dict': network.state_dict(),
            'optimizer': optimizer.state_dict(),
            'train_error': train_loss,
            'test_error': test_loss
        }
        if test_loss < prev_test_error:
            print("Updating the checkpoint with better model")
            save_ckp(checkpoint, args.result_dir, comb_filename)
            prev_test_error = test_loss
        else:
            print("Skipping the checkpoint update. Model not better than the previous best")
        adjust_learning_rate(optimizer, t)

