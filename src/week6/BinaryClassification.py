import torch
from args import *
import numpy as np
from torch.utils.data import Dataset
from bpemb import BPEmb
# import nni
import matplotlib.pyplot as plt
import torch.utils.data as Data
import pandas as pd
from datasets import load_dataset
import transformers
from transformers import GPT2Tokenizer, GPT2Model
from sklearn.metrics import classification_report, confusion_matrix
from transformers import AutoTokenizer
from model import *

device = torch.device("cpu")
if torch.cuda.is_available():
    device = torch.device("cuda")
args = get_args()

import torch
from torch import nn
from datasets import load_dataset, load_metric

# squad_v2等于True或者False分别代表使用SQUAD v1 或者 SQUAD v2。
# 如果您使用的是其他数据集，那么True代表的是：模型可以回答“不可回答”问题，也就是部分问题不给出答案，而False则代表所有问题必须回答。
squad_v2 = False
# distilbert-base-uncased can only be used in English
model_checkpoint = "distilbert-base-uncased"
# model_checkpoint = "bert-base-multilingual-uncased"
batch_size = 16

dataset = load_dataset("copenlu/answerable_tydiqa")

from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(model_checkpoint)

max_length = 128  # 输入feature的最大长度，question和context拼接之后
doc_stride = 64  # 2个切片之间的重合token数量。

pad_on_right = True


def getLanguageDataSet(data, language):
    def printAndL(x):
        return x["language"] == language

    return data.filter(printAndL)


def getEnglishDataSet(data):
    return getLanguageDataSet(data, "english")


def getJapDataSet(data):
    return getLanguageDataSet(data, "japanese")


def getFinDataSet(data):
    return getLanguageDataSet(data, "finnish")


# keep only english data
english_set = getEnglishDataSet(dataset)

english_set = english_set.remove_columns("language")

english_set = english_set.remove_columns("document_url")


def prepare_train_features(examples):
    # 既要对examples进行truncation（截断）和padding（补全）还要还要保留所有信息，所以要用的切片的方法。
    # 每一个一个超长文本example会被切片成多个输入，相邻两个输入之间会有交集。
    tokenized_examples = tokenizer(
        examples["question_text" if pad_on_right else "document_plaintext"],
        examples["document_plaintext" if pad_on_right else "question_text"],
        truncation="only_second" if pad_on_right else "only_first",
        max_length=max_length,
        stride=doc_stride,
        return_overflowing_tokens=True,
        return_offsets_mapping=True,
        padding="max_length",
    )

    # 我们使用overflow_to_sample_mapping参数来映射切片片ID到原始ID。
    # 比如有2个expamples被切成4片，那么对应是[0, 0, 1, 1]，前两片对应原来的第一个example。
    sample_mapping = tokenized_examples.pop("overflow_to_sample_mapping")
    # offset_mapping也对应4片
    # offset_mapping参数帮助我们映射到原始输入，由于答案标注在原始输入上，所以有助于我们找到答案的起始和结束位置。
    offset_mapping = tokenized_examples.pop("offset_mapping")

    # 重新标注数据
    tokenized_examples["label"] = []

    for i, offsets in enumerate(offset_mapping):
        # 对每一片进行处理
        # 将无答案的样本标注到CLS上
        input_ids = tokenized_examples["input_ids"][i]

        # 区分question和context
        sequence_ids = tokenized_examples.sequence_ids(i)

        # 拿到原始的example 下标.
        sample_index = sample_mapping[i]
        answers = examples["annotations"][sample_index]
        # 如果没有答案，则使用CLS所在的位置为答案.
        if len(answers["answer_start"]) == [-1]:
            tokenized_examples["label"].append([0])
        else:
            # 答案的character级别Start/end位置.
            start_char = answers["answer_start"][0]
            end_char = start_char + len(answers["answer_text"][0])

            # 找到token级别的index start.
            token_start_index = 0
            while sequence_ids[token_start_index] != (1 if pad_on_right else 0):
                token_start_index += 1

            # 找到token级别的index end.
            token_end_index = len(input_ids) - 1
            while sequence_ids[token_end_index] != (1 if pad_on_right else 0):
                token_end_index -= 1

            # 检测答案是否超出文本长度，超出的话也适用CLS index作为标注.
            if not (offsets[token_start_index][0] <= start_char and offsets[token_end_index][1] >= end_char):
                tokenized_examples["label"].append([0])
            else:
                tokenized_examples["label"].append([1])

    return tokenized_examples


def get_torch_vec(examples):
    train_data = {}
    train_data["input_ids"] = torch.tensor(examples["input_ids"]).cuda()
    train_data["attention_mask"] = torch.tensor(examples["attention_mask"]).cuda()
    train_data["label"] = torch.tensor(examples["label"]).cuda()
    return train_data


tokenized_datasets = english_set.map(prepare_train_features, batched=True,
                                     remove_columns=english_set["train"].column_names)
tokenized_datasets = tokenized_datasets.map(get_torch_vec)

train_data = tokenized_datasets['train']
test_data = tokenized_datasets['validation']

train_loader = Data.DataLoader(dataset=train_data,
                               batch_size=128,
                               shuffle=True)

test_loader = Data.DataLoader(dataset=test_data,
                              batch_size=16,
                              shuffle=True)

criterion = torch.nn.CrossEntropyLoss(reduction="mean")  # loss function

model = QA_model(args.input_dim, args.hidden_dim, args.output_dim).to('cuda')
optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, amsgrad=True)

max_acc = 0
losses = []

for epoch in range(args.epochs):
    model.train()
    batch_num = 0

    for batch in train_loader:
        label = torch.stack(batch['label'], dim=1).cuda()
        label = torch.squeeze(label, dim=-1).cuda()
        predict_label = model(batch)
        loss = criterion(predict_label, label)

        pred = predict_label.max(-1, keepdim=True)[1]
        acc = pred.eq(label.view_as(pred)).sum().item() / predict_label.shape[0]
        optimizer.zero_grad()
        if (acc > max_acc):
            max_acc = acc
            torch.save(model.state_dict(), 'model_week3_en.pth')
        loss.backward()
        optimizer.step()
        batch_num += 1
        # nni.report_intermediate_result(max_acc)
        print("epoch:", epoch + 1, "batch_num:", batch_num, "loss:", round(loss.item(), 4), "acc:", acc)
        losses.append(loss.item())

plt.plot([i for i in range(len(losses))], losses)
plt.savefig("loss_week3.png")
print("train_max_acc:", max_acc)
model = QA_model(args.input_dim, args.hidden_dim, args.output_dim).to('cuda')
model.load_state_dict(torch.load("model_week3_en.pth"))

count_acc = 0
max_test_acc = 0
count = 0
real_labels = 0
predict_labels = 0
for batch in test_loader:
    label = torch.stack(batch['label'], dim=0).cuda().squeeze(0)
    predict_label = model(batch)
    if (count == 0):
        real_labels = label.cpu().detach().numpy()
        predict_labels = predict_label.max(-1, keepdim=True)[1].squeeze(1).cpu().detach().numpy()
    else:
        real_labels = np.hstack((real_labels, label.cpu().detach().numpy()))
        predict_labels = np.hstack(
            (predict_labels, predict_label.max(-1, keepdim=True)[1].squeeze(1).cpu().detach().numpy()))
    count += 1
    # pred = predict_label.max(-1,keepdim=True)[1]
    # test_acc = pred.eq(label.view_as(pred)).sum().item()/predict_label.shape[0]
    # count+=1
    # max_test_acc = max(test_acc,max_test_acc)
    # count_acc+= test_acc

# real = np.concatenate(real_labels,axis=1)
# predict_labels = np.array(predict_labels)
report = classification_report(predict_labels, real_labels, output_dict=True)
print(pd.DataFrame(report).transpose())


