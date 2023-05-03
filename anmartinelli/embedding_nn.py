import torch.nn as nn
import torch

class embedding_cat_variables(nn.Module):
    # at the moment cat_past and cat_fut together
    def __init__(self, seq_len: int, lag: int, d_model: int, emb_dims: list, device):
        """Class for embedding categorical variables, adding 3 positional variables during forward

        Args:
            seq_len (int): length of the sequence (sum of past and future steps)
            lag (int): number of future step to be predicted
            d_model (int): dimension of all variables after they are embedded
            emb_dims (list): size of the dictionary for embedding. One dimension for each categorical variable
            device : -
        """
        super().__init__()
        self.seq_len = seq_len
        self.lag = lag
        self.device = device
        self.cat_embeds = emb_dims + [seq_len, lag+1, 2] # 
        self.cat_n_embd = nn.ModuleList([
            nn.Embedding(emb_dim, d_model) for emb_dim in self.cat_embeds
        ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """All components of x are concatenated with 3 new variables for data augmentation, in the order:
        - pos_seq: assign at each step its time-position
        - pos_fut: assign at each step its future position. 0 if it is a past step
        - is_fut: explicit for each step if it is a future(1) or past one(0)

        Args:
            x (torch.Tensor): [bs, seq_len, num_vars]

        Returns:
            torch.Tensor: [bs, seq_len num_vars+3, n_embd] 
        """
        B, _, _ = x.shape
        pos_seq = self.get_pos_seq(bs=B)
        pos_fut = self.get_pos_fut(bs=B)
        is_fut = self.get_is_fut(bs=B)
        cat_vars = torch.cat((x, pos_seq, pos_fut, is_fut),dim=2)
        cat_n_embd = self.get_cat_n_embd(cat_vars)
        return cat_n_embd

    def get_pos_seq(self, bs):
        pos_seq = torch.arange(0, self.seq_len)
        pos_seq = pos_seq.repeat(bs,1).unsqueeze(2).to(self.device)
        return pos_seq
    
    def get_pos_fut(self, bs):
        pos_fut = torch.cat((torch.zeros((self.seq_len-self.lag), dtype=torch.long),torch.arange(1,self.lag+1)))
        pos_fut = pos_fut.repeat(bs,1).unsqueeze(2).to(self.device)
        return pos_fut
    
    def get_is_fut(self, bs):
        is_fut = torch.cat((torch.zeros((self.seq_len-self.lag), dtype=torch.long),torch.ones((self.lag), dtype=torch.long)))
        is_fut = is_fut.repeat(bs,1).unsqueeze(2).to(self.device)
        return is_fut
    
    def get_cat_n_embd(self, cat_vars):
        cat_n_embd = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.cat_n_embd):
            emb = layer(cat_vars[:, :, index])
            cat_n_embd = torch.cat((cat_n_embd, emb.unsqueeze(2)),dim=2)
        return cat_n_embd
    
class embedding_target(nn.Module):
    def __init__(self, d_model: int):
        """Class for embedding target variable (Only one)

        Args:
            d_model (int): -
        """
        super().__init__()
        self.y_lin = nn.Linear(1, d_model, bias = False)

    def forward(self, y: torch.Tensor) -> torch.Tensor:
        """Embedding the target varible. (Only one)

        Args:
            y (torch.Tensor): [bs, seq_len, 1] past and future steps of scaled target variable

        Returns:
            torch.Tensor: [bs, seq_len, d_model]
        """
        y = self.y_lin(y.float())
        return y

class GLU(nn.Module):
    # sub net of GRN 
    def __init__(self, d_model: int):
        """Gated Linear Unit, 'Gate' block in TFT paper 

        Args:
            d_model (int): -
        """
        super().__init__()
        self.linear1 = nn.Linear(d_model, d_model)
        self.linear2 = nn.Linear(d_model, d_model)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Gated Linear Unit

        Args:
            x (torch.Tensor): [bs, seq_len, d_model]

        Returns:
            torch.Tensor: [bs, seq_len, d_model]
        """
        x1 = self.sigmoid(self.linear1(x))
        x2 = self.linear2(x)
        out = x1*x2 #element-wise multiplication
        return out
    
class GRN(nn.Module):
    def __init__(self, d_model: int, dropout: float):
        """Gated Residual Network

        Args:
            d_model (int): -
            dropout (float): -
        """
        super().__init__()
        self.linear1 = nn.Linear(d_model, d_model) 
        self.elu = nn.ELU()
        self.linear2 = nn.Linear(d_model, d_model)
        self.dropout = nn.Dropout(dropout)
        self.glu = GLU(d_model)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Gated Residual Network

        Args:
            x (torch.Tensor): [bs, seq_len, d_model]

        Returns:
            torch.Tensor: [bs, seq_len, d_model]
        """
        eta1 = self.elu(self.linear1(x))
        eta2 = self.dropout(self.linear2(eta1))
        out = self.norm(x + self.glu(eta2))
        return out

class flatten_GRN(nn.Module):
    def __init__(self, emb_dims: list, dropout: float):
        """Modified GRN for flattened variables 

        Args:
            emb_dims (list): [start_emb: int, mid_emb: int, end_emb: int] list of int for dimensions
            dropout (float): -
        """
        super().__init__()
        start_emb, mid_emb, end_emb = emb_dims
        self.res_conn = nn.Linear(start_emb, end_emb, bias = False)
        self.dropout_res_conn = nn.Dropout(dropout)
        self.linear1 = nn.Linear(start_emb, mid_emb, bias = False) 
        self.elu = nn.ELU()
        self.linear2 = nn.Linear(mid_emb, end_emb, bias = False)
        self.dropout = nn.Dropout(dropout)
        self.glu = GLU(end_emb)
        self.norm = nn.LayerNorm(end_emb)
        self.softmax = nn.Softmax(dim=2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Modified GRN for flattened variables

        Args:
            x (torch.Tensor): [bs, seq_len, emb_dims[0]]

        Returns:
            torch.Tensor: [bs, seq_len, emb_dims[-1]]
        """
        res_conn = self.dropout_res_conn(self.res_conn(x))
        eta1 = self.elu(self.linear1(x))
        eta2 = self.dropout(self.linear2(eta1))
        out = self.norm(res_conn + self.glu(eta2))
        out = self.softmax(out)
        return out

class Encoder_Var_Selection(nn.Module): # input already embedded
    def __init__(self, use_target_past: bool, n_past_cat_var: int, n_past_tar_var: int, d_model: int, dropout: float, device):
        """Variable Selection Network in Encoder(past)

        Args:
            use_target_past (bool): True if we want to use the past target variable mixing it to past variables, False to use only past vars
            n_past_cat_var (int): number of categorical variables for past steps
            n_past_tar_var (int): number of target variables for past steps. If use_target_past==False it is ignored
            d_model (int): -
            dropout (float): -
            device: -
        """
        super().__init__()
        self.use_target_past = use_target_past
        self.device = device
        #categorical
        self.n_grn_cat = n_past_cat_var
        self.GRNs_cat = nn.ModuleList([
            GRN(d_model, dropout) for _ in range(self.n_grn_cat)
        ])
        tot_var = n_past_cat_var
        # if using target past
        if use_target_past:
            self.n_grn_tar = n_past_tar_var
            self.GRNs_num = nn.ModuleList([
                GRN(d_model, dropout) for _ in range(self.n_grn_tar)
            ])
            tot_var = tot_var + n_past_tar_var
        #flatten
        emb_dims = [d_model*tot_var, int((d_model+tot_var)/2), tot_var]
        self.flatten_GRN = flatten_GRN(emb_dims, dropout)

    def forward(self, categorical: torch.Tensor, y: torch.Tensor=None) -> torch.Tensor:
        """NN for Selecting Importance of Past Variables passed to the Model

        Args:
            categorical (torch.Tensor): [bs, past_steps, n_cat_var, d_model] past_cat_variables to be selected
            y (torch.Tensor, optional): [bs, past_steps, d_model]. Defaults to None.

        Returns:
            torch.Tensor: [bs, past_steps, d_model]
        """
        # categorical var_selection
        var_sel = self.get_cat_GRN(categorical)
        to_be_flat = categorical
        if y is not None:
            assert self.use_target_past==True # you don't have y if mix is not True
            num_var_sel = self.get_num_GRN(y)
            var_sel = torch.cat((var_sel, num_var_sel), dim = 2)
            to_be_flat = torch.cat((to_be_flat, y), dim=2)

        var_sel_wei = self.get_flat_GRN(to_be_flat)
        out = var_sel*var_sel_wei.unsqueeze(3)
        out = torch.sum(out, 2)/out.shape[2]
        return out

    def get_cat_GRN(self, x: torch.Tensor) -> torch.Tensor:
        cat_after_GRN = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.GRNs_cat):
            grn = layer(x[:,:,index,:])
            cat_after_GRN = torch.cat((cat_after_GRN, grn.unsqueeze(2)), dim=2)
        return cat_after_GRN
    
    def get_num_GRN(self, x: torch.Tensor) -> torch.Tensor:
        num_after_GRN = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.GRNs_num):
            grn = layer(x[:,:,index,:])
            num_after_GRN = torch.cat((num_after_GRN, grn.unsqueeze(2)), dim=2)
        return num_after_GRN
    
    def get_flat_GRN(self, to_be_flat: torch.Tensor) -> torch.Tensor:
        emb = torch.flatten(to_be_flat, start_dim=2)
        var_sel_wei = self.flatten_GRN(emb)
        return var_sel_wei
class Encoder_LSTM(nn.Module):
    def __init__(self, n_layers_LSTM: int, d_model: int, dropout: float, device):
        """LSTM Encoder with GLU, Add and Norm

        Args:
            n_layers_EncLSTM (int): number of layers involved by LSTM 
            d_model (int): -
            dropout (float): -
            device: -
        """
        super().__init__()
        self.device = device
        self.n_layers_EncLSTM = n_layers_LSTM
        self.hidden_size = d_model
        self.LSTM = nn.LSTM(input_size=d_model, hidden_size=self.hidden_size, num_layers=self.n_layers_EncLSTM, batch_first = True)
        self.dropout = nn.Dropout(dropout)
        self.LSTM_enc_GLU = GLU(d_model)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, x: torch.Tensor) -> list:
        """LSTM Encoder with GLU, Add and Norm

        Args:
            x (torch.Tensor): [bs, past_steps, d_model]

        Returns:
            list of tensors: [output_enc, hn, cn] where hn and cn must be used for Decoder_LSTM. 
        """
        h0 = torch.zeros(self.n_layers_EncLSTM, x.size(0), x.size(2)).to(self.device)
        c0 = torch.zeros(self.n_layers_EncLSTM, x.size(0), x.size(2)).to(self.device)
        lstm_enc, (hn, cn) = self.LSTM(x, (h0,c0))
        lstm_enc = self.dropout(lstm_enc)
        output_enc = self.norm(self.LSTM_enc_GLU(lstm_enc) + x)
        return [output_enc, hn, cn]
    
class Decoder_Var_Selection(nn.Module): # input already embedded
    def __init__(self, use_yprec: bool, n_fut_cat_var: int, n_fut_tar_var: int, d_model: int, dropout: float, device):
        """Variable Selection Network in Decoder(future)

        Args:
            use_yprec (bool): True if we want to use the last predicted values of target variable(s)
            n_fut_cat_var (int): number of categorical variables for future steps
            n_fut_tar_var (int): number of target variables for future steps. If use_yprec==False it is ignored
            d_model (int): -
            dropout (float): -
            device: -
        """
        super().__init__()
        self.use_yprec = use_yprec
        self.device = device
        #categorical
        self.n_grn_cat = n_fut_cat_var
        self.GRNs_cat = nn.ModuleList([
            GRN(d_model, dropout) for _ in range(self.n_grn_cat)
        ])
        tot_var = n_fut_cat_var
        #numerical
        if use_yprec:
            self.n_grn_num = n_fut_tar_var
            self.GRNs_num = nn.ModuleList([
                GRN(d_model, dropout) for _ in range(self.n_grn_num)
            ])
            tot_var = tot_var+n_fut_tar_var
        #flatten
        emb_dims = [d_model*tot_var, int((d_model+tot_var)/2), tot_var]
        self.flatten_GRN = flatten_GRN(emb_dims, dropout)

    def forward(self, categorical: torch.Tensor, y: torch.Tensor=None) -> torch.Tensor:
        """Variable Selection Network in Decoder(future)

        Args:
            categorical (torch.Tensor): [bs, past_steps, n_cat_var, d_model] fut_cat_variables to be selected
            y (torch.Tensor, optional): [bs, past_steps, d_model]. Defaults to None.

        Returns:
            torch.Tensor: [bs, past_steps, d_model]
        """
        var_sel = self.get_cat_GRN(categorical)
        to_be_flat = categorical
        if y is not None:
            assert self.use_yprec==True
            num_after_GRN = self.get_num_GRN(y)
            var_sel = torch.cat((var_sel, num_after_GRN), dim = 2)
            to_be_flat = torch.cat((to_be_flat, y), dim=2)
        var_sel_wei = self.get_flat_GRN(to_be_flat)
        out = var_sel*var_sel_wei.unsqueeze(3)
        out = torch.sum(out, 2)/out.size(2)
        return out

    def get_cat_GRN(self, x):
        cat_after_GRN = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.GRNs_cat):
            grn = layer(x[:,:,index,:])
            cat_after_GRN = torch.cat((cat_after_GRN, grn.unsqueeze(2)), dim=2)
        return cat_after_GRN
    
    def get_num_GRN(self, x):
        num_after_GRN = torch.Tensor().to(self.device)
        for index, layer in enumerate(self.GRNs_num):
            grn = layer(x[:,:,index,:])
            num_after_GRN = torch.cat((num_after_GRN, grn.unsqueeze(2)), dim=2)
        return num_after_GRN
    
    def get_flat_GRN(self, to_be_flat: torch.Tensor) -> torch.Tensor:
        # apply flatten_GRN and softmax
        emb = torch.flatten(to_be_flat, start_dim=2)
        var_sel_wei = self.flatten_GRN(emb)
        return var_sel_wei
    
class Decoder_LSTM(nn.Module):
    def __init__(self, n_layers_LSTM: int, d_model: int, dropout: float):
        """LSTM Decoder with GLU, Add and Norm

        Args:
            n_layers_LSTM (int): number of layers involved by LSTM 
            d_model (int): -
            dropout (float): -
        """
        super().__init__()
        self.n_layers_DecLSTM = n_layers_LSTM
        self.hidden_size = d_model
        self.LSTM = nn.LSTM(input_size=d_model, hidden_size=self.hidden_size, num_layers=self.n_layers_DecLSTM, batch_first = True)
        self.dropout = nn.Dropout(dropout)
        self.LSTM_enc_GLU = GLU(d_model)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, x: torch.Tensor, hn: torch.Tensor, cn: torch.Tensor) -> torch.Tensor:
        """LSTM Decoder with GLU, Add and Norm

        Args:
            x (torch.Tensor): [bs, past_steps, d_model] main Tensor
            hn (torch.Tensor): [n_layers_DecLSTM, bs, d_model] Tensor of hidden states from Encoder
            cn (torch.Tensor): [n_layers_DecLSTM, bs, d_model] Tensor of initial cell states from Encoder

        Returns:
            torch.Tensor: [bs, past_steps, d_model]
        """
        lstm_dec, _ = self.LSTM(x, (hn,cn))
        lstm_dec = self.dropout(lstm_dec)
        output_dec = self.norm(self.LSTM_enc_GLU(lstm_dec) + x)
        return output_dec

class postTransformer(nn.Module):
    def __init__(self, d_model: int, dropout: float):
        """Last part of TFT after decoder and before last linear

        Args:
            d_model (int): -
            dropout (float): -
        """
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        self.GLU1 = GLU(d_model)
        self.norm1 = nn.LayerNorm(d_model)
        self.GRN = GRN(d_model, dropout)
        self.GLU2 = GLU(d_model)
        self.norm2 = nn.LayerNorm(d_model)
    
    def forward(self, x: torch.Tensor, res_conn_dec: torch.Tensor, res_conn_grn: torch.Tensor) -> torch.Tensor:
        """_summary_

        Args:
            x (torch.Tensor): [bs, past_steps, d_model] main Tensor
            res_conn_dec (torch.Tensor): [bs, past_steps, d_model] residual connection pre decoder
            res_conn_grn (torch.Tensor): [bs, past_steps, d_model] residual connection pre GRN-Static Enrichment

        Returns:
            torch.Tensor: [bs, past_steps, d_model]
        """
        x = self.dropout(x)
        x = self.norm1(res_conn_dec + self.GLU1(x))
        x = self.GRN(x)
        out = self.norm2(res_conn_grn + self.GLU2(x))
        return out
    
if __name__=='__main__':
    from dataloading import dataloading
    from sklearn.preprocessing import StandardScaler

    bs = 8
    bs_test = 4
    seq_len = 265
    lag = 65
    hour = 24
    hour_test = 24
    train = True
    step = 1
    scaler_type = StandardScaler()
    path_data = '/home/andrea/timeseries/data/edison/processed.pkl' 
    train_dl, _, _, _ = dataloading(batch_size=bs, batch_size_test=bs_test, 
                                                        seq_len=seq_len, lag=lag,
                                                        hour_learning=hour, 
                                                        hour_inference=hour_test, 
                                                        train_bool=train,
                                                        step = step,
                                                        scaler_y = scaler_type,
                                                        path=path_data)
    
    x, y = next(iter(train_dl))
    # x.shape = [8, 256, 6]
    # y.shape = [8, 256]
    
    # tft = True
    d_model = 4
    n_enc = 2
    n_dec = 2
    head_size = 2
    num_heads = 2
    fw_exp = 3
    device = 'cpu'
    dropout = 0.1
    n_layers = 3

    categorical = x[:,:,1:]

    # start embedding
    emb_dims = [12+1, 31+1, 24, 7, 3]
    n_cat_var = len(emb_dims) +3 # added by embedding_cat_variables
    n_target_var = 1
    emb_cat_var = embedding_cat_variables(seq_len, lag, d_model, emb_dims, device)
    emb_y_var = embedding_target(d_model)
    embed_x = emb_cat_var(categorical) #                        
    embed_y = emb_y_var(y.unsqueeze(dim=2)).unsqueeze(dim=2) #                  

    tot_var = n_cat_var + n_target_var

    embed_x_past = embed_x[:,:-lag,:,:]
    embed_y_past = embed_y[:,:-lag,:,:]
    embed_x_fut = embed_x[:,-lag:,:,:]
    embed_y_fut = embed_y[:,-lag:,:,:]

    # # init NN
    from encoder import Encoder
    from decoder import Decoder
    use_target_past = False
    var_sel_enc = Encoder_Var_Selection(use_target_past, n_cat_var, n_target_var, d_model, dropout, device)
    lstm_enc = Encoder_LSTM(n_layers, d_model, dropout, device)
    grn_enc = GRN(d_model, dropout)
    encoder = Encoder(n_enc, d_model, num_heads, head_size, fw_exp, dropout)

    use_yprec = False
    var_sel_dec = Decoder_Var_Selection(use_yprec, n_cat_var, n_target_var, d_model, dropout, device)
    lstm_dec = Decoder_LSTM(n_layers, d_model, dropout)
    grn_dec = GRN(d_model, dropout)
    decoder = Decoder(n_dec, d_model, num_heads, head_size, fw_exp, lag, dropout)

    var_sel_past = var_sel_enc(embed_x_past)
    lstm_encs, hn, cn = lstm_enc(var_sel_past)
    pre_enc = grn_enc(lstm_encs)
    encoding = encoder(embed_y_past.squeeze(2), pre_enc, pre_enc)

    # DECODER USING ONLY x (prec variable to handle the difference)
    var_sel_fut = var_sel_dec(embed_x_fut)
    lstm_decs = lstm_dec(var_sel_fut, hn, cn)
    pre_dec = grn_dec(lstm_decs)
    decoding = decoder(pre_dec, encoding, encoding)

    post_transformer = postTransformer(d_model, dropout)
    out = post_transformer(decoding, pre_dec, lstm_decs)
    
    quantile = True
    if quantile:
        quantiles = [0.1, 0.5, 0.9]
        loss = [0]*len(quantiles)
        target = torch.randint(-2, 2, (bs, lag))
        # last_linear in model.py
        out_linear = nn.Linear(d_model, len(quantiles))
        out = out_linear(out)
        for i, q in enumerate(quantiles):
            q_loss = torch.max(q*(target - out[:,:,i]), (1-q)*(out[:,:,i] - target))
            loss[i] = loss[i] + q_loss