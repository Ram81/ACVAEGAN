import argparse
import numpy as np
import torch
import torchvision.datasets as dset
import torchvision.transforms as transforms

from network import VAEGAN
from tensorboardX import SummaryWriter
from torch.autograd import Variable

from torch.optim import RMSprop, Adam, SGD
from torch.optim.lr_scheduler import ExponentialLR, MultiStepLR

import progressbar
from utils import RollingMeasure


np.random.seed(8)
torch.manual_seed(8)
torch.cuda.manual_seed(8)

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="VAEGAN")
    parser.add_argument("--train_folder", action="store", dest="train_folder")
    parser.add_argument("--test_folder", action="store", dest="test_folder")
    parser.add_argument("--n_epochs", default=12, action="store", type=int, dest="n_epochs")
    parser.add_argument("--z_size", default=128, action="store", type=int, dest="z_size")
    parser.add_argument("--recon_level", default=3, action="store", type=int, dest="recon_level")
    parser.add_argument("--lambda_mse", default=1e-3, action="store", type=float, dest="lambda_mse")
    parser.add_argument("--lr", default=3e-4, action="store", type=float, dest="lr")
    parser.add_argument("--decay_lr", default=0.75, action="store", type=float, dest="decay_lr")
    parser.add_argument("--decay_mse", default=1, action="store", type=float, dest="decay_mse")
    parser.add_argument("--decay_margin",default=1,action="store",type=float, dest="decay_margin")
    parser.add_argument("--decay_equilibrium", default=1, action="store", type=float, dest="decay_equilibrium")
    parser.add_argument("--slurm", default=False, action="store", type=bool, dest="slurm")
    parser.add_argument("--batchsize", default=64, action="store", type=int, dest="batchsize")

    args = parser.parse_args()

    train_folder = args.train_folder
    test_folder = args.test_folder
    z_size = args.z_size
    recon_level = args.recon_level
    decay_mse = args.decay_mse
    decay_margin = args.decay_margin
    n_epochs = args.n_epochs
    lambda_mse = args.lambda_mse
    lr = args.lr
    decay_lr = args.decay_lr
    decay_equilibrium = args.decay_equilibrium
    slurm = args.slurm
    batchsize = args.batchsize

    # TODO: add to argument parser
    dataset_name = 'cifar10'

    writer = SummaryWriter(comment="_CIFAR10_GAN")
    net = VAEGAN(z_size=z_size, recon_level=recon_level).cuda()

    # DATASET
    if dataset_name == 'cifar10':
        dataset = dset.CIFAR10(
            root=train_folder, download=True,
            transform=transforms.Compose([
                transforms.Scale(z_size),
                transforms.ToTensor(),
                transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
            ]))
        dataloader = torch.utils.data.DataLoader(dataset, batch_size=64,
                                                 shuffle=True, num_workers=4)

    # margin and equilibirum
    margin = 0.35
    equilibrium = 0.68

    # mse_lambda = 1.0
    # OPTIM-LOSS
    # an optimizer for each of the sub-networks, so we can selectively backprop
    # optimizer_encoder = Adam(params=net.encoder.parameters(),lr = lr,betas=(0.9,0.999))

    optimizer_encoder = RMSprop(params=net.encoder.parameters(), lr=lr, alpha=0.9, eps=1e-8, weight_decay=0, momentum=0,
                                centered=False)
    # lr_encoder = MultiStepLR(optimizer_encoder,milestones=[2],gamma=1)
    lr_encoder = ExponentialLR(optimizer_encoder, gamma=decay_lr)
    # optimizer_decoder = Adam(params=net.decoder.parameters(),lr = lr,betas=(0.9,0.999))
    optimizer_decoder = RMSprop(params=net.decoder.parameters(), lr=lr, alpha=0.9, eps=1e-8, weight_decay=0, momentum=0,
                                centered=False)
    lr_decoder = ExponentialLR(optimizer_decoder, gamma=decay_lr)
    # lr_decoder = MultiStepLR(optimizer_decoder,milestones=[2],gamma=1)
    # optimizer_discriminator = Adam(params=net.discriminator.parameters(),lr = lr,betas=(0.9,0.999))
    optimizer_discriminator = RMSprop(params=net.discriminator.parameters(), lr=lr, alpha=0.9, eps=1e-8, weight_decay=0,
                                      momentum=0, centered=False)
    lr_discriminator = ExponentialLR(optimizer_discriminator, gamma=decay_lr)
    # lr_discriminator = MultiStepLR(optimizer_discriminator,milestones=[2],gamma=1)

    batch_number = len(dataloader)
    step_index = 0
    widgets = [

        'Batch: ', progressbar.Counter(),
        '/', progressbar.FormatCustomText('%(total)s', {"total": batch_number}),
        ' ', progressbar.Bar(marker="-", left='[', right=']'),
        ' ', progressbar.ETA(),
        ' ',
        progressbar.DynamicMessage('loss_nle'),
        ' ',
        progressbar.DynamicMessage('loss_encoder'),
        ' ',
        progressbar.DynamicMessage('loss_decoder'),
        ' ',
        progressbar.DynamicMessage('loss_discriminator'),
        ' ',
        progressbar.DynamicMessage('loss_mse_layer'),
        ' ',
        progressbar.DynamicMessage('loss_kld'),
        ' ',
        progressbar.DynamicMessage('loss_aux_classifier'),
        ' ',
        progressbar.DynamicMessage("epoch")
    ]

    # for each epoch
    if slurm:
        print(args)

    for i in range(n_epochs):

        progress = progressbar.ProgressBar(min_value=0, max_value=batch_number + 1, initial_value=0,
                                           widgets=widgets).start()
        # reset rolling average
        loss_nle_mean = RollingMeasure()
        loss_encoder_mean = RollingMeasure()
        loss_decoder_mean = RollingMeasure()
        loss_discriminator_mean = RollingMeasure()
        loss_reconstruction_layer_mean = RollingMeasure()
        loss_kld_mean = RollingMeasure()
        loss_aux_classifier_mean = RollingMeasure()
        gan_gen_eq_mean = RollingMeasure()
        gan_dis_eq_mean = RollingMeasure()
        # print("LR:{}".format(lr_encoder.get_lr()))

        # for each batch
        for j, (data_batch, target_batch) in enumerate(dataloader):

            # set to train mode
            net.train()
            # target and input are the same images

            data_target = Variable(target_batch, requires_grad=False).float().cuda()
            data_in = Variable(data_batch, requires_grad=False).float().cuda()
            aux_label_batch = Variable(target_batch, requires_grad=False).long().cuda()

            # get output
            out, out_labels, out_layer, mus, variances, aux_labels = net(data_in)
            # split so we can get the different parts
            out_layer_predicted = out_layer[:len(out_layer) // 2]
            out_layer_original = out_layer[len(out_layer) // 2:]
            # TODO set a batch_len variable to get a clean code here
            out_labels_original = out_labels[:len(out_labels) // 2]
            out_labels_sampled = out_labels[-len(out_labels) // 2:]

            # aux labels for original and actual images
            aux_labels_original = aux_labels[:len(aux_labels) // 2]
            aux_labels_sampled = aux_labels[-len(aux_labels) // 2:]

            # loss, nothing special here
            nle_value, kl_value, mse_value, bce_dis_original_value, bce_dis_sampled_value, \
            bce_gen_original_value, bce_gen_sampled_value, \
            nllloss_aux_original, nllloss_aux_sampled = VAEGAN.loss(data_target, out,
                                                                    out_layer_original,
                                                                    out_layer_predicted,
                                                                    out_labels_original,
                                                                    out_labels_sampled,
                                                                    mus, variances,
                                                                    aux_labels_original, aux_labels_sampled,
                                                                    aux_label_batch)
            # THIS IS THE MOST IMPORTANT PART OF THE CODE
            loss_encoder = torch.sum(kl_value) + torch.sum(mse_value)
            loss_discriminator = torch.sum(bce_dis_original_value) + torch.sum(bce_dis_sampled_value) \
                                 + torch.sum(nllloss_aux_original) + torch.sum(nllloss_aux_sampled)

            loss_decoder = torch.sum(lambda_mse * mse_value) - loss_discriminator
            # loss_decoder = torch.sum(mse_lambda * mse_value) + (1.0-mse_lambda)*(torch.sum(bce_gen_sampled_value)
            # +torch.sum(bce_gen_original_value))

            # register mean values of the losses for logging
            loss_nle_mean(torch.mean(nle_value).data.cpu().numpy())

            loss_discriminator_mean((torch.mean(bce_dis_original_value) + torch.mean(bce_dis_sampled_value)
                                     + torch.mean(nllloss_aux_original) + torch.mean(
                        nllloss_aux_sampled)).data.cpu().numpy())

            loss_decoder_mean((torch.mean(lambda_mse * mse_value) - (
                        torch.mean(bce_dis_original_value) + torch.mean(bce_dis_sampled_value))).data.cpu().numpy())
            # loss_decoder_mean((torch.mean(mse_lambda * mse_value) + (1-mse_lambda)*(
            # torch.mean(bce_gen_original_value) + torch.mean(bce_gen_sampled_value))).data.cpu().numpy()[0])

            loss_encoder_mean((torch.mean(kl_value) + torch.mean(mse_value)).data.cpu().numpy())
            loss_reconstruction_layer_mean(torch.mean(mse_value).data.cpu().numpy())
            loss_kld_mean(torch.mean(kl_value).data.cpu().numpy())
            loss_aux_classifier_mean(
                (torch.mean(nllloss_aux_original) + torch.mean(nllloss_aux_sampled)).data.cpu().numpy())

            # selectively disable the decoder of the discriminator if they are unbalanced
            train_dis = True
            train_dec = True
            if torch.mean(bce_dis_original_value).data < equilibrium - margin or torch.mean(
                    bce_dis_sampled_value).data < equilibrium - margin:
                train_dis = False
            if torch.mean(bce_dis_original_value).data > equilibrium + margin or torch.mean(
                    bce_dis_sampled_value).data > equilibrium + margin:
                train_dec = False
            if train_dec is False and train_dis is False:
                train_dis = True
                train_dec = True

            # aggiungo log
            if train_dis:
                gan_dis_eq_mean(1.0)
            else:
                gan_dis_eq_mean(0.0)

            if train_dec:
                gan_gen_eq_mean(1.0)
            else:
                gan_gen_eq_mean(0.0)

            # BACKPROP
            # clean grads
            net.zero_grad()
            # encoder
            loss_encoder.backward(retain_graph=True)
            # someone likes to clamp the grad here
            # [p.grad.data.clamp_(-1,1) for p in net.encoder.parameters()]
            # update parameters
            optimizer_encoder.step()
            # clean others, so they are not afflicted by encoder loss
            net.zero_grad()
            # decoder
            if train_dec:
                loss_decoder.backward(retain_graph=True)
                # [p.grad.data.clamp_(-1,1) for p in net.decoder.parameters()]
                optimizer_decoder.step()
                # clean the discriminator
                net.discriminator.zero_grad()
            # discriminator
            if train_dis:
                loss_discriminator.backward()
                # [p.grad.data.clamp_(-1,1) for p in net.discriminator.parameters()]
                optimizer_discriminator.step()

            # LOGGING
            if slurm:
                progress.update(progress.value + 1, loss_nle=loss_nle_mean.measure,
                                loss_encoder=loss_encoder_mean.measure,
                                loss_decoder=loss_decoder_mean.measure,
                                loss_discriminator=loss_discriminator_mean.measure,
                                loss_mse_layer=loss_reconstruction_layer_mean.measure,
                                loss_kld=loss_kld_mean.measure,
                                loss_aux_classifier=loss_aux_classifier_mean.measure,
                                epoch=i + 1)

        # EPOCH END
        if slurm:
            progress.update(progress.value + 1, loss_nle=loss_nle_mean.measure,
                            loss_encoder=loss_encoder_mean.measure,
                            loss_decoder=loss_decoder_mean.measure,
                            loss_discriminator=loss_discriminator_mean.measure,
                            loss_mse_layer=loss_reconstruction_layer_mean.measure,
                            loss_kld=loss_kld_mean.measure,
                            loss_aux_classifier=loss_aux_classifier_mean.measure,
                            epoch=i + 1)
        lr_encoder.step()
        lr_decoder.step()
        lr_discriminator.step()
        margin *= decay_margin
        equilibrium *= decay_equilibrium
        # margin non puo essere piu alto di equilibrium
        if margin > equilibrium:
            equilibrium = margin
        lambda_mse *= decay_mse
        if lambda_mse > 1:
            lambda_mse = 1
        progress.finish()

        writer.add_scalar('loss_encoder', loss_encoder_mean.measure, step_index)
        writer.add_scalar('loss_decoder', loss_decoder_mean.measure, step_index)
        writer.add_scalar('loss_discriminator', loss_discriminator_mean.measure, step_index)
        writer.add_scalar('loss_reconstruction', loss_nle_mean.measure, step_index)
        writer.add_scalar('loss_kld', loss_kld_mean.measure, step_index)
        writer.add_scalar('loss_aux_classifier', loss_aux_classifier_mean.measure, step_index)
        writer.add_scalar('gan_gen', gan_gen_eq_mean.measure, step_index)
        writer.add_scalar('gan_dis', gan_dis_eq_mean.measure, step_index)
        step_index += 1

    exit(0)
