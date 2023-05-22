import os
import pickle
import argparse
from configparser import ConfigParser

from datetime import datetime

import torch
from torch.utils.data import DataLoader

from model import GAT

from train import training
from inference import get_plot
from test import test
from data_manipulation.data_generation import MyDataset
    
split = {'start_train': datetime(2017,11,15,0), 
        'end_train':datetime(2020,1,1,0),
        'start_validation':datetime(2020,1,1,0), 
        'end_validation':datetime(2020,12,1,0),
        'start_test': datetime(2021,3,5,0), 
        'end_test':datetime(2022,4,6,0)}

def train(config: ConfigParser) -> None:
    
    print(f'{" Upload the dataframe ":=^60s}')
    batch_size = config.getint('dataset', 'batch_size')
    path_dataset = os.path.join(config['paths']['dataset'], f"dataframes.pkl")    
    path_emb = os.path.join(config['paths']['dataset'], f"embedding_setting.pkl")    
    
    
    with open(path_dataset, 'rb') as f :
        ds = pickle.load(f)

    with open(path_emb, 'rb') as f :
        emb = pickle.load(f)
        
    dl = {}  
    for key in ds.keys():
        dl[key] = DataLoader(ds[key], 
                             batch_size = batch_size, 
                             shuffle = True)
        
        config['dataset'][f'n_obs_{key}']=f"{len(ds[key])}"
    
    print(f'{" Upload complete ":=^60s}')


    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    params_model = {}
    for key in list(dict(config.items('model')).keys()):
        if key != "id_model":
            try:
                params_model[key] = config.getint('model', key)
            except:
                params_model[key] = config.getfloat('model', key)

    id_model = config['model']['id_model']
    
    print(f'{f" Configuration (hid_out_features1, hid_out_features2, num_layer1, num_layer2, nfeat_out_gnn, lr, weight_decay) = ({id_model})":=^60s}')
    model = GAT(in_feat = params_model["in_feat"], 
                nfeat_out_gnn = params_model["nfeat_out_gnn"],
                num_layer1=params_model["num_layer1"],
                num_layer2=params_model["num_layer2"],
                hid_out_features1 = params_model["hid_out_features1"],
                hid_out_features2 = params_model["hid_out_features2"],
                emb = emb,
                past = 200, 
                future = 65, 
                device = device)
    
    model.to(model.device)
    optimizer = torch.optim.Adam(model.parameters(),    
                             lr = config.getfloat('optimizer','lr'), 
                             weight_decay = config.getfloat('optimizer','weight_decay'))
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, 
                                                       gamma = config.getfloat('optimizer','gamma'))
    training(model = model, 
            dataloader_train = dl['train'], 
            dataloader_validation = dl['validation'],
            optimizer = optimizer,
            scheduler = scheduler, 
            config = config)

    print(f'{" Creating the plot for the LAG ":=^60s}')

    path = os.path.join(config['paths']['net_weights_train'], f"GNN_{config['model']['id_model']}.pt")
    model.load_state_dict(torch.load(path))
    
    get_plot(model = model, 
             config = config,
             ds = ds)    


if __name__ == "__main__":
    # all standard stuffs here
    parser = argparse.ArgumentParser(description="Base model")
    parser.add_argument("-c", "--config", type=str, required = True, help="Config file")

    # parametri extra
    parser.add_argument("-lr", "--lr", type=str, help = "learning rate of the optimization step")
    parser.add_argument("-nl1", "--num_layer1", type=str, help = "number of layer of GNN")
    parser.add_argument("-nl2", "--num_layer2", type=str, help = "number of layer of GNN")
    parser.add_argument("-hl1", "--hid_out_features1", type=str, help = "hidden layer firts GNN")
    parser.add_argument("-hl2", "--hid_out_features2", type=str, help = "hidden layer second GNN")
    parser.add_argument("-hfg", "--nfeat_out_gnn", type=str, help = "hidden layer for the output of the GNN")
    parser.add_argument("-wd", "--weight_decay", type=str, help = "weight decay")

    # non posso avere più di un'operazione alla volta
    parser.add_argument("-d", "--dataset", action="store_true", help = "data manipulation")
    parser.add_argument("-t", "--train", action="store_true", help="Train model flag")
    parser.add_argument("-i", "--inference", action="store_true", help="Inference model flag")
    parser.add_argument("-e", "--test", action="store_true", help="Inference model flag")

    parser.add_argument("-p", "--get_plot", action="store_true", help="Train model flag")
    parser.add_argument("-m", "--name_model", type=str, help="Train model flag")
    

    args = parser.parse_args()
    config_path = args.config
    config = ConfigParser()
    config.read(config_path)
    
    print(f'{" Cheking the paths ":=^60s}')
    if 'paths' in config.keys():
        for path in config['paths']:
            if os.path.exists(config['paths'][path]) != True:
                os.makedirs(config['paths'][path])

    print(f'{" Paths controlled ":=^60s}')
    
    print(f'{" Setting new possible parameters for the model ":=^60s}')
    config['model']['id_model'] = ""
    for i, key in enumerate(["hid_out_features1", "hid_out_features2", "num_layer1", "num_layer2", "nfeat_out_gnn", "lr", "weight_decay"]):
        exists = True
        try:
            getattr(args, key)
        except:
            exists = False
        if exists:
            if getattr(args, key) is not None:
                if key not in ["lr", "weight_decay"]:
                    config['model'][key] = f"{getattr(args, key)}"
                else:
                    config['optimizer'][key] = f"{getattr(args, key)}"

        if i ==0:
            config['model']['id_model'] = f"{config['model'][key]}" 
        else:
            if key not in ["lr", "weight_decay"]:
                config['model']['id_model'] = f"{config['model']['id_model']}_{config['model'][key]}" 
            else:
                config['model']['id_model'] = f"{config['model']['id_model']}_{config['optimizer'][key]}" 
    
    
    print(f'{" Setting complete ":=^60s}')
    if args.get_plot:
        models = os.listdir(config['paths']['net_weights_train'])
        for n, model in enumerate(models):
            if os.path.exists(os.path.join(config['paths']['prediction'], f'adj_{model[4:-3]}.pkl')):
                print(model[4:])
                test(config = config, 
                     name_model = model)
    if args.dataset:
        os.system(f"python3 data_manipulation/data_generation.py --config {config_path}")
    if args.train:
        train(config)