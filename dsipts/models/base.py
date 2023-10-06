
from torch import optim
import torch
import pytorch_lightning as pl
from torch.optim.lr_scheduler import StepLR
from abc import  abstractmethod
from .utils import SinkhornDistance, SoftDTWBatch,PathDTWBatch,pairwise_distances
from ..data_structure.utils import beauty_string

def standardize_momentum(x,order):
    mean = torch.mean(x,1).unsqueeze(1).repeat(1,x.shape[1],1)
    num=torch.pow(x-mean,order).mean(axis=1)
    den=torch.pow(x-mean,order-1).mean(axis=1)
    den = torch.pow(den,order*1.0/order-1)
    return num/den


def dilate_loss(outputs, targets, alpha, gamma, device):
	# outputs, targets: shape (batch_size, N_output, 1)
	batch_size, N_output = outputs.shape[0:2]
	loss_shape = 0
	softdtw_batch = SoftDTWBatch.apply
	D = torch.zeros((batch_size, N_output,N_output )).to(device)
	for k in range(batch_size):
		Dk = pairwise_distances(targets[k,:,:].view(-1,1),outputs[k,:,:].view(-1,1))
		D[k:k+1,:,:] = Dk     
	loss_shape = softdtw_batch(D,gamma)
	
	path_dtw = PathDTWBatch.apply
	path = path_dtw(D,gamma)           
	Omega =  pairwise_distances(torch.range(1,N_output).view(N_output,1)).to(device)
	loss_temporal =  torch.sum( path*Omega ) / (N_output*N_output) 
	loss = alpha*loss_shape+ (1-alpha)*loss_temporal
	return loss#, loss_shape, loss_temporal


class Base(pl.LightningModule):
    @abstractmethod
    def __init__(self,verbose:bool):
        """
        This is the basic model, each model implemented must overwrite the init method and the forward method. The inference step is optional, by default it uses the forward method but for recurrent 
        network you should implement your own method
        """
        
        super(Base, self).__init__()
        self.save_hyperparameters(logger=False)
        self.count_epoch = 0
        self.initialize = False
        self.train_loss_epoch = -100.0
        self.verbose = verbose
    @abstractmethod
    def forward(self, batch:dict)-> torch.tensor:
        """Forlward method used during the training loop

        Args:
            batch (dict): the batch structure. The keys are:
                y : the target variable(s). This is always present
                x_num_past: the numerical past variables. This is always present
                x_num_future: the numerical future variables
                x_cat_past: the categorical past variables
                x_cat_future: the categorical future variables
                idx_target: index of target features in the past array
            

        Returns:
            torch.tensor: output of the mode;
        """
        return None
    
    def inference(self, batch:dict)->torch.tensor:
        """Usually it is ok to return the output of the forward method but sometimes not (e.g. RNN)

        Args:
            batch (dict): batch

        Returns:
            torch.tensor: result
        """
        return self(batch)
        
    def configure_optimizers(self):
        """
        Each model has optim_config and scheduler_config
        
        :meta private:
        """
        
        if self.optim_config is None:
            self.optim_config = {'lr': 5e-05}

        
        if self.optim is None:
            optimizer = optim.Adam(self.parameters(),  **self.optim_config)
            self.initialize = True
        else:
            ##this is strange, pytorch lighening call twice this if autotune is true
            if self.initialize is False:
                self.optim = eval(self.optim)
            beauty_string(self.optim,'',self.verbose)
            optimizer = self.optim(self.parameters(),  **self.optim_config)
            self.initialize = True
        self.lr = self.optim_config['lr']
        if self.scheduler_config is not None:
            scheduler = StepLR(optimizer,**self.scheduler_config)
            return [optimizer], [scheduler]
        else:
            return optimizer


    def training_step(self, batch, batch_idx):
        """
        pythotrch lightening stuff
        
        :meta private:
        """
        y_hat = self(batch)
        return self.compute_loss(batch,y_hat)
    
    def validation_step(self, batch, batch_idx):
        """
        pythotrch lightening stuff
        
        :meta private:
        """
        y_hat = self(batch)
        return self.compute_loss(batch,y_hat)


    def validation_epoch_end(self, outs):
        """
        pythotrch lightening stuff
        
        :meta private:
        """
    
        loss = torch.stack(outs).mean()
        self.log("val_loss", loss.item(),sync_dist=True)
        beauty_string(f'Epoch: {self.count_epoch} train error: {self.train_loss_epoch:.4f} validation loss: {loss.item():.4f}','info',self.verbose)

    def training_epoch_end(self, outs):
        """
        pythotrch lightening stuff
        
        :meta private:
        """

        loss = sum(outs['loss'] for outs in outs) / len(outs)
        self.log("train_loss", loss.item(),sync_dist=True)
        self.count_epoch+=1
        self.train_loss_epoch = loss.item()
        #logging.info(f'Epoch: {self.count_epoch}, train loss: {loss.item():.4f}')

    def compute_loss(self,batch,y_hat):
        """
        custom loss calculation
        
        :meta private:
        """

        if self.use_quantiles is False:
            initial_loss = self.loss(y_hat[:,:,:,0], batch['y'])
        else:
            initial_loss = self.loss(y_hat, batch['y'])
        x =  batch['x_num_past'].to(self.device)
        idx_target = batch['idx_target'][0]
        x_start = x[:,-1,idx_target].unsqueeze(1)
        y_persistence = x_start.repeat(1,self.future_steps,1)
        
        ##generally you want to work without quantile loss
        if self.use_quantiles is False:
            x = y_hat[:,:,:,0]
        else:
            x = y_hat[:,:,:,1]
        

        if self.loss_type == 'linear_penalization':
            persistence_error = self.persistence_weight*(2.0-10.0*torch.clamp( torch.abs((y_persistence-x)/(0.001+torch.abs(y_persistence))),min=0.0,max=0.1))
            loss = torch.mean(torch.abs(x- batch['y'])*persistence_error)
            
        elif self.loss_type == 'exponential_penalization':
            weights = (1+self.persistence_weight*torch.exp(-torch.abs(y_persistence-x)))
            loss =  torch.mean(torch.abs(x- batch['y'])*weights)
         
        elif self.loss_type=='sinkhorn':
            sinkhorn = SinkhornDistance(eps=0.1, max_iter=100, reduction='mean')
            loss = sinkhorn.compute(x,batch['y'])

            
        elif self.loss == 'additive_iv':
            std = torch.sqrt(torch.var(batch['y'], dim=(1))+ 1e-8) ##--> BSxChannel
            x_std = torch.sqrt(torch.var(x, dim=(1))+ 1e-8)
            loss = torch.mean( torch.abs(x-batch['y']).mean(axis=1).flatten() + self.persistence_weight*torch.abs(x_std-std).mean(axis=1).flatten())
            
        elif self.loss == 'multiplicative_iv':
            std = torch.sqrt(torch.var(batch['y'], dim=(1))+ 1e-8) ##--> BSxChannel
            x_std = torch.sqrt(torch.var(x, dim=(1))+ 1e-8)
            loss = torch.mean( torch.abs(x-batch['y']).mean(axis=1).flatten()*torch.abs(x_std-std).mean(axis=1).flatten())   
              
        elif self.loss_type=='global_iv':
            std_real = torch.sqrt(torch.var(batch['y'], dim=(0,1)))
            std_predict = torch.sqrt(torch.var(x, dim=(0,1)))
            loss = initial_loss +  self.persistence_weight*torch.abs(std_real-std_predict)

        elif self.loss_type=='smape':
            loss = torch.mean(2*torch.abs(x-batch['y']) / (torch.abs(x)+torch.abs(batch['y'])))
            
        elif self.loss_type=='triplet':
            loss_fn = torch.nn.TripletMarginLoss(margin=0.1, p=1.0,swap=False)
            loss = initial_loss +  self.persistence_weight*loss_fn(x, batch['y'], y_persistence)
                
        elif self.loss_type=='high_order':
            loss = initial_loss
            for i in range(2,5):
                mom_real = standardize_momentum( batch['y'],i)
                mom_pred = standardize_momentum(x,i)
                mom_loss = torch.abs(mom_real-mom_pred).mean()
                loss+=self.persistence_weight*mom_loss
            
        elif self.loss_type=='dilated':
            #BxLxCxMUL
            alpha = 0.5
            gamma = 0.01
            loss = 0
            ##no multichannel here
            for i in range(y_hat.shape[2]):
                ##error here
                
                loss+= dilate_loss( batch['y'][:,:,i:i+1],x[:,:,i:i+1], alpha, gamma, y_hat.device)
            
            
        else:
            loss = initial_loss



        return loss