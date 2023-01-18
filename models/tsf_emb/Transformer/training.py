import torch
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm
import sys

def train_step(net, data_loader, lag, optimizer, cost_function, device): 
    cumulative_loss = 0.
    net.train()
    
    # iterate over the training set
    for i, (ds, y, low) in enumerate(tqdm(data_loader, desc = "train step")):
        
        y = y.to(device)
        y_clone = y.detach().clone().to(device)
        ds = ds.to(device)
        low = low.to(device)
        output = net(ds,y_clone,low)

        output = output.squeeze().float()
        y = y[:,-lag:].float()

        loss = cost_function(output,y)
        loss.backward()
        optimizer.step()
        optimizer.zero_grad()
        cumulative_loss += loss.item()
        
        y.to('cpu')
        ds.to('cpu')
        low.to('cpu')
        output.to('cpu')
            
    return cumulative_loss/(i+1)

def test_step(net, data_loader, lag, cost_function, device):
    cumulative_loss = 0.
    net.eval() 

    with torch.no_grad():
        for i, (ds, y,low) in enumerate(tqdm(data_loader, desc = "test step")):
            
            y = y.to(device)
            y_clone = y.detach().clone().to(device)
            ds = ds.to(device)
            low = low.to(device)
            output = net(ds,y_clone,low)
            
            output = output.squeeze().float()
            y = y[:,-lag:].float()

            loss = cost_function(output, y)
            cumulative_loss += loss.item()
            
            y.to('cpu')
            ds.to('cpu')
            low.to('cpu')
            output.to('cpu')

    print('-'*50)
    print(f'PRED: \n{output[0]}')
    print('-'*50)
    print(f'REAL: \n{y[0]}')
    print('-'*50)
            
    return cumulative_loss/(i+1)

def training(net, device, loader_train, loader_val, path_best, path_last,
        lag:int = 60, lr:float = 1e-06, wd:float = 0.00, scheduler_step:int = 150, epochs:int = 1000):
    # import pdb
    # pdb.set_trace()
    path_str = path_best[:-11].split('/')
    writer_log_dir = '/'.join(path_str[:-1])
    writer_scalars = path_str[-1]
    
    writer = SummaryWriter(log_dir=writer_log_dir)

    def get_cost_function():
        cost_function = torch.nn.L1Loss(reduction='mean')
        return cost_function

    def get_optimizer(net, lr, wd):
        optimizer = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=wd)
        return optimizer

    optimizer = get_optimizer(net=net, lr=lr, wd=wd)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=scheduler_step, gamma=0.8) # GAMMA SCHEDULER STEP
    cost_function = get_cost_function()

    print(f'\n---> Pre-Training <---')
    #* 
    pre_val_loss = test_step(net=net, data_loader=loader_val, lag=lag, cost_function=cost_function, device=device)
    
    best_val_loss = pre_val_loss

    last_model = torch.save(net.state_dict(), path_last)
    best_model = torch.save(net.state_dict(), path_best)
    val_loss=pre_val_loss
    best_val_loss=float('inf')
    str_min = f'[{0}]: {pre_val_loss:.5f}'

    print('-> TRAINING <-')
    for e in range(epochs):

        sys.stdout.flush()

        train_loss = train_step(net=net, data_loader=loader_train, lag=lag, optimizer=optimizer, cost_function=cost_function, device=device)
        
        if e%10 == 9:
            val_loss = test_step(net=net, data_loader=loader_val, lag=lag, cost_function=cost_function, device=device)
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_model = torch.save(net.state_dict(), path_best)
                str_min = f'[{e+1}]: {best_val_loss:.5f}'

        writer.add_scalars(writer_scalars, {'train_loss':train_loss, 'val_loss':val_loss}, e+1)
        last_model = torch.save(net.state_dict(), path_last)
    
        scheduler.step()
        print(f'Epoch: [{e+1}]: Training Loss: {train_loss:.5f}\tValidation Loss: {val_loss:.5f} (min->{str_min})')

    print('\n-  AFTER TRAINING  ----------------------------------------------------')
    post_val_loss = test_step(net=net, data_loader=loader_val, lag=lag, cost_function=cost_function, device=device)
    
    print(f'\t Pre Validation loss = {pre_val_loss:.5f}. After {e+1} epochs -> {post_val_loss:.5f}')
    print('-----------------------------------------------------')
    
    writer.close()

## SAVING
    # torch.save({
        # 'epoch': epoch,
        # 'model_state_dict': model.state_dict(),
        # 'optimizer_state_dict': optimizer.state_dict(),
        # 'loss': loss,
        # ...
        # }, PATH)

## LOADING
    # model = TheModelClass(*args, **kwargs)
    # optimizer = TheOptimizerClass(*args, **kwargs)

    # checkpoint = torch.load(PATH)
    # model.load_state_dict(checkpoint['model_state_dict'])
    # optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    # epoch = checkpoint['epoch']
    # loss = checkpoint['loss']

    # model.eval()
    # # - or -
    # model.train()

# Save on CPU, Load on GPU

# Save:
# torch.save(model.state_dict(), PATH)

# Load:
# device = torch.device("cuda")
# model = TheModelClass(*args, **kwargs)
# model.load_state_dict(torch.load(PATH, map_location="cuda:0"))  # Choose whatever GPU device number you want
# model.to(device)
# # Make sure to call input = input.to(device) on any input tensors that you feed to the model