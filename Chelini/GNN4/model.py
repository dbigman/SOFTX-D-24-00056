import torch
import torch.nn as nn

class pre_processing(torch.nn.Module):
    def __init__(self, 
                 in_feat:int, 
                 hid:int,
                 emb:dict, 
                 device):
        super(pre_processing, self).__init__()
        self.hid = hid
        self.in_feat = in_feat
        self.emb = nn.ModuleDict({})
        out = 0
        for key in emb.keys():
            self.emb[key] = nn.Embedding(num_embeddings = emb[key][0],
                                         embedding_dim = emb[key][1], 
                                         device=device)
            out += emb[key][1]
        
        # sommo le tre variabili non categoriche dell'input
        out += 3
        
        self.linear = nn.Sequential(nn.Linear(in_features = out, 
                                              out_features = 128),
                                    nn.ReLU(), 
                                    nn.Linear(in_features = 128,
                                              out_features = 128),
                                    nn.ReLU(),
                                    nn.Linear(in_features = 128,
                                              out_features = out-1)
        )
        
        
    def forward(self, x):
        out = []
        for i,key in enumerate(self.emb.keys()):
            out.append(self.emb[key](x[:, :,i].int().to(x.device.type)))
        out = torch.cat(out,-1)    
        out = torch.cat((out, x[:,:,-3:]),-1)
        out = self.linear(out.float())
        
        out = torch.cat((out, x[:,:,-1:]),-1)
        return out.float()

    
class Kernel(torch.nn.Module):
    def __init__(self, 
                 d: int,
                 hid: int, 
                 past: int,
                 future: int
                ):
        super(Kernel, self).__init__()
        
        # Siccome non posso generare una matrice simmetrica definita positiva 
        # costruisco una matrice "d x hid" che poi trasformo in simmetrica definita positiva
        self.d = d
        self.hid = hid
        self.past = past
        self.future = future
        self.softmax = nn.Softmax(dim = 1)
        # Parametri da imparare
        self.smoothing = torch.nn.Parameter(torch.randn(1))
        self.W = torch.nn.Parameter(torch.randn(self.d, self.hid))
        
        self.sig = nn.Sigmoid()
        
    def forward(self, x, y, A):
        x_p = x[:,:self.past,:]
        x_f = x[:,self.past:,:]
        B, P, O = x_p.shape
        B, P1, O = x_f.shape
        
        # now the matrix is positive definite
        theta = torch.matmul(self.W, self.W.T)
        theta = (theta+theta.T)/2
        
        diff = x_p.view(B,1,P,O)-x_f.view(B,P1,1,O)
        w = torch.matmul(diff, theta)
        w = torch.matmul(w,torch.transpose(diff, -2,-1))
        w = -0.5*w/(self.sig(self.smoothing)*0.01)
        
        # B, F, P, P ---> B, F, P
        # indico i pesi di ogni osservazione del passato per quanto riguarda la i-esima osservazione nel futuro
        alpha = torch.diagonal(w,dim1=-1, dim2=-2)
        # devo fare la trasposta perchè i vettori sono per riga e non per colonna
        A_tmp = A[self.past:,:self.past]
        A_tmp = self.softmax(A_tmp.masked_fill(A_tmp == 0, float('-inf')))
        alpha = alpha.masked_fill(A_tmp < 4e-3, float('-inf'))
        alpha = self.softmax(alpha)
        yh = torch.matmul(alpha,y)
        
        return yh

class my_gcnn(torch.nn.Module):

    def __init__(self, 
                 in_channels: int, 
                 out_channels: int,
                 nodes: int,
                 relu: bool = True):

        super(my_gcnn, self).__init__()

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.nodes = nodes
        self.lin = nn.Linear(in_channels, 
                             out_channels, 
                             bias = False)
        self.I = torch.diag(torch.ones(self.nodes))
        self.relu = nn.ReLU()
        if relu:
            self.act = nn.ReLU()
        else:
            self.act = nn.Identity()
        

    def forward(self,
                x0: tuple) -> torch.tensor:
        x, A = x0
        
        # Inserisco una threshold
        # se i valori sono più piccoli di un tot significa che non ci è connessione
        D = torch.sign(self.relu(A - 1e-15))
        D = torch.diag((1+torch.sum(D , 1))**(-0.5))

        A_tilde = A + self.I.to(A.device.type)

        x = self.lin(x)
        out = torch.matmul(D, A)
        out = torch.matmul(out,D)
        out = self.act(torch.matmul(out,x))
        
        return (out.float(), A)
    
class GAT(torch.nn.Module):
    def __init__(self, 
                in_feat:int, 
                hid:int,  
                past: int, 
                future: int,
                emb:dict,
                device,
                num_layer1:int = None,
                num_layer2:int = None,
                hid_out_features1:int = None,
                hid_out_features2:int = None):
        
        super(GAT, self).__init__()
        
        self.device = device
        
        self.hid = hid
        self.in_feat = in_feat         # numero di features di ogni nodo prima del primo GAT        
        self.past = past 
        self.future = future
        # B = batch size
        # I = features in the input
        # O = out preprocessing
        # O'= out gat
        # F = Future
        # P = Past
        # H = hid

        
        ########## PREPROCESSING PART #############
        # devo convertire le variabili categoriche
        # B, T, I ---> B, T, O
        # T = tokens, quindi T in (P,F)
        
        out = 3
        for key in emb.keys():
            out += emb[key][1]
        self.out_preprocess = out
        self.pre_processing = pre_processing(in_feat = in_feat, 
                                            hid = hid,
                                            emb = emb, 
                                            device = device)
        
        N = past + future 
        d = N//3
        self.M = torch.nn.Parameter(torch.randn(N, d)) # Matrice di adiacenza
        self.mask = torch.tril(torch.ones(N, N))-torch.diag(torch.ones(N))
        self.relu = nn.ReLU()
        self.softmax = nn.Softmax(dim = 1)
        
        
        ########## FIRST GAT PART #############
        # B * P, O ---> B * T, O-1
        if num_layer1 in [0,1]:
            self.gnn1 = my_gcnn(in_channels = self.out_preprocess, 
                                out_channels = self.out_preprocess-1, 
                                nodes = past,
                                relu = False)     
        else:
            layers = [my_gcnn(in_channels = self.out_preprocess, 
                                      out_channels = hid_out_features1,
                                      nodes = past)]
            
            for _ in range(max(0,num_layer1-2)):
                layers.append(my_gcnn(in_channels = hid_out_features1, 
                                      out_channels = hid_out_features1,
                                      nodes = past))
                
            layers.append(my_gcnn(in_channels = hid_out_features1, 
                                  out_channels = self.out_preprocess-1, 
                                  nodes = past,
                                  relu= False))    
            
            self.gnn1 = nn.Sequential(*layers)

        ########## KERNELL PART #############
        # B, P, O-1 ---> B, P, H
        
        self.kernel = Kernel(d = self.out_preprocess-1, 
                            hid = self.hid, 
                            past = self.past, 
                            future = self.future)
        
        # weight in R^(B, P, F)
        # interpreto la diffusione dell'informazione tramite colonna
        # la colonna indica quanto i nodi del passato influiscano sul un determinato nodo del futuro
        # tipo matrice di Markov
        
        ########## SECOND GAT PART #############
        
        if num_layer2 in [0, 1]:
            self.gnn2 = my_gcnn(in_channels = self.out_preprocess, 
                                out_channels = 1, 
                                nodes = future,
                                relu = False)
        else:
            layers = [my_gcnn(in_channels = self.out_preprocess, 
                                      out_channels = hid_out_features2,
                                      nodes = future)]
            
            for _ in range(max(0,num_layer2-2)):
                layers.append(my_gcnn(in_channels = hid_out_features2, 
                                      out_channels = hid_out_features2,
                                      nodes = future))
                
            layers.append(my_gcnn(in_channels = hid_out_features2, 
                                  out_channels = 1, 
                                  nodes = future,
                                  relu = False))    
            
            self.gnn2 = nn.Sequential(*layers)
                        
    def forward(self, data):
        
        # estraggo i due sottografi      
        # il primo è riferito al passato 
        # il secondo è riferito al futuro
        
        A = self.relu(torch.matmul(self.M,self.M.T))
        A = self.softmax(A.masked_fill(self.mask.to(A.device.type) == 0, float('-inf')))
        A = A.masked_fill(self.mask.to(A.device.type) == 0, 0)
        # pre-processing dei dati
        x = self.pre_processing(data)
        
        
        sg1 = x[:,:self.past,:]
        sg2 = x[:,self.past:,:]

        ########## FIRST GAT PART ######################
        x1,_ = self.gnn1((sg1, A[:self.past, :self.past]))
        ########## KERNELL PART ########################
        x_kernel = torch.cat((x1, sg2[:,:,:-1]),-2)
        y_kernel = x[:,:self.past,-1:]

        yh = self.kernel(x_kernel,y_kernel, A)
        x2 = torch.cat((sg2[:,:,:-1],yh),-1)
        ########## SECOND GAT PART ######################
        out, _ = self.gnn2((x2, A[self.past:, self.past:]))
        out = out.reshape(-1,self.future)
        
        
        ########## PREPARE THE DIRICHLET ENERGY ##########
        x = torch.cat((sg1, x2),-2)
        x = torch.cdist(x,x)        
        
        return out.float(), x, A