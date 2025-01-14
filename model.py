import torch
from torch import nn
import numpy as np
# import matplotlib.pyplot as plt
from torch.autograd import Variable


def seq_max_pool(x):
    """seq是[None, seq_len, s_size]的格式，
    mask是[None, seq_len, 1]的格式，先除去mask部分，
    然后再做maxpooling。
    """
    seq, mask = x
    seq = seq - (1 - mask) * 1e10
    return torch.max(seq, 1)


def seq_and_vec(x):
    """seq是[None, seq_len, s_size]的格式，
    vec是[None, v_size]的格式，将vec重复seq_len次，拼到seq上，
    得到[None, seq_len, s_size+v_size]的向量。
    """
    seq, vec = x
    vec = torch.unsqueeze(vec, 1)

    vec = torch.zeros_like(seq[:, :, :1]).cuda() + vec
    return torch.cat([seq, vec], 2)


def seq_gather(x):
    """seq是[None, seq_len, s_size]的格式，
    idxs是[None, 1]的格式，在seq的第i个序列中选出第idxs[i]个向量，
    最终输出[None, s_size]的向量。
    """
    seq, idxs = x
    batch_idxs = torch.arange(0, seq.size(0))

    batch_idxs = torch.unsqueeze(batch_idxs, 1).cuda()

    idxs = torch.cat([batch_idxs, idxs], 1)

    res = []
    for i in range(idxs.size(0)):
        vec = seq[idxs[i][0], idxs[i][1], :]
        res.append(torch.unsqueeze(vec, 0))

    res = torch.cat(res)
    return res


class s_model(nn.Module):
    def __init__(self, word_dict_length, word_emb_size, lstm_hidden_size):
        super(s_model, self).__init__()

        self.embeds = nn.Embedding(word_dict_length, word_emb_size)
        self.fc1_dropout = nn.Sequential(
            nn.Dropout(0.25),  # drop 20% of the neuron
        )

        self.lstm1 = nn.LSTM(
            input_size=word_emb_size,
            hidden_size=int(word_emb_size / 2),
            num_layers=1,
            batch_first=True,
            bidirectional=True
        )

        self.lstm2 = nn.LSTM(
            input_size=word_emb_size,
            hidden_size=int(word_emb_size / 2),
            num_layers=1,
            batch_first=True,
            bidirectional=True
        )

        self.conv1 = nn.Sequential(
            nn.Conv1d(
                in_channels=word_emb_size * 2,  # 输入的深度
                out_channels=word_emb_size * 2,  # filter 的个数，输出的高度
                kernel_size=3,  # filter的长与宽
                stride=1,  # 每隔多少步跳一下
                padding=1,  # 周围围上一圈 if stride= 1, pading=(kernel_size-1)/2
            ),
            nn.ReLU(),
        )
        self.fc_ps1 = nn.Sequential(
            nn.Linear(word_emb_size * 2, 1),
        )

        self.fc_ps2 = nn.Sequential(
            nn.Linear(word_emb_size * 2, 1),
        )

    def forward(self, t):
        mask = torch.gt(torch.unsqueeze(t, 2), 0).type(torch.FloatTensor)  # (batch_size,sent_len,1)
        mask.requires_grad = False
        # mask torch.Size([21, 126, 1])
        outs = self.embeds(t)
        # outs torch.Size([21, 126, 128])
        t = outs
        t = self.fc1_dropout(t)
        mask = mask.cuda()
        t = t.mul(mask)  # (batch_size,sent_len,char_size)
        # t torch.Size([21, 126, 128])
        # mask torch.Size([21, 126, 1])
        # mul矩阵对应位置相乘 mm矩阵相乘 此时t中LongTensor=0的补丁embed值被全部mask成0
        t, (h_n, c_n) = self.lstm1(t, None)
        t, (h_n, c_n) = self.lstm2(t, None)

        t_max, t_max_index = seq_max_pool([t, mask])

        t_dim = list(t.size())[-1]
        h = seq_and_vec([t, t_max])

        # h = h.permute(0, 2, 1)
        #
        # h = self.conv1(h)
        #
        # h = h.permute(0, 2, 1)
        conv_res = self.conv1(h.permute(0, 2, 1))
        h = h + conv_res.permute(0, 2, 1)

        ps1 = self.fc_ps1(h)
        ps2 = self.fc_ps2(h)

        # torch.Size([21, 126, 1])
        # torch.Size([21, 126, 1])
        # torch.Size([21, 126, 128])
        # torch.Size([21, 128])
        # torch.Size([21, 126, 1])
        return [ps1, ps2, t, t_max, mask]


class po_model(nn.Module):
    def __init__(self, word_dict_length, word_emb_size, lstm_hidden_size, num_classes):
        super(po_model, self).__init__()

        self.conv1 = nn.Sequential(
            nn.Conv1d(
                in_channels=word_emb_size * 4,  # 输入的深度
                out_channels=word_emb_size * 4,  # filter 的个数，输出的高度
                kernel_size=3,  # filter的长与宽
                stride=1,  # 每隔多少步跳一下
                padding=1,  # 周围围上一圈 if stride= 1, pading=(kernel_size-1)/2
            ),
            nn.ReLU(),
        )

        self.fc_ps1 = nn.Sequential(
            nn.Linear(word_emb_size * 4, num_classes + 1),
            # nn.Softmax(),
        )

        self.fc_ps2 = nn.Sequential(
            nn.Linear(word_emb_size * 4, num_classes + 1),
            # nn.Softmax(),
        )
        self.relu = nn.ReLU()

    def forward(self, t, t_max, k1, k2):
        k1 = seq_gather([t, k1])

        k2 = seq_gather([t, k2])
        # k1 k2就是把表示位置的整数型k1 k2转换为向量形式
        k = torch.cat([k1, k2], 1)
        # k torch.Size([21, 256]) 蕴含了subject首尾的字向量
        h = seq_and_vec([t, t_max])
        # t.shape,t_max.shape,h.shape
        # (torch.Size([21, 126, 128]), torch.Size([21, 128]), torch.Size([21, 126, 256]))
        h = seq_and_vec([h, k])
        # h此时变为21 126 h.size(2)+k.size(2)=512

        # h此时为[bs,channel,seq_len]
        conv_res = self.conv1(h.permute(0, 2, 1))
        h = h + conv_res.permute(0, 2, 1)
        # h此时torch.Size([21, 126, 128])
        h = self.relu(h)
        po1 = self.fc_ps1(h)
        po2 = self.fc_ps2(h)

        return [po1, po2]
