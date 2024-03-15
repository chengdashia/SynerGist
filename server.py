"""
    服务器执行文件，主要负责：
        1、根据客户端的资源使用情况。将模型文件进行分层
        ip及对应节点位序
"""
import torch
from torch import nn
import logging
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
import config
from node_end import NodeEnd
from models.vgg.vgg import VGG
from models.model_struct import model_cfg
from utils.segment_strategy import NetworkSegmentationStrategy
from utils.resource_utilization import get_all_server_info
from utils.utils import get_client_app_port


def convert_node_layer_indices(node_to_layer):
    """
    将节点层索引字典转换为层节点映射字典
    :param node_to_layer: 节点层索引字典,键为节点IP,值为该节点对应的层索引列表
    :return: 层节点映射字典,键为层索引,值为对应的节点IP
    """
    # 初始化层节点映射字典
    layer_node_mapping = {}

    # 遍历节点层索引字典中的每个节点
    for node_ip, layer_indices in node_to_layer.items():
        # 遍历该节点对应的层索引列表
        for layer_idx in layer_indices:
            # 将层索引和对应的节点IP添加到层节点映射字典
            layer_node_mapping[layer_idx] = node_ip

    return layer_node_mapping


def calculate_accuracy(fx, y):
    """
    计算模型输出与真实标签之间的准确率

    参数:
    fx (Tensor): 模型输出
    y (Tensor): 真实标签

    返回值:
    acc (float): 准确率(0-100)
    """
    # 计算预测值，fx是模型输出，y是真实标签
    predictions = fx.max(1, keepdim=True)[1]
    # 将预测值和真实标签转换为相同形状
    correct = predictions.eq(y.view_as(predictions)).sum()
    # 计算准确率，correct是预测正确的样本数量
    acc = 100.00 * correct.float() / predictions.shape[0]
    return acc


def node_inference(node, model):
    """
    节点推理的主要逻辑。它接收来自其他节点的数据和信息，计算输出，然后将结果发送给下一个节点。如果是最后一层，它会计算损失。
    :param node:
    :param model:
    :return:
    """
    # 重新初始化节点
    node.__init__(host_ip, host_port)
    while True:
        global reverse_split_layer, split_layer
        # 存储已发送的IP地址
        next_clients = []
        # 迭代次数
        iterations = int(config.N / config.B)
        # 等待连接
        node_socket, node_addr = node.wait_for_connection()
        # 迭代处理每一批数据
        for i in range(iterations):
            logging.info(f"node_{host_node_num} 获取来自 node{node_addr} 的连接")
            msg = node.receive_message(node_socket)
            logging.info("msg: %s", msg)
            # 解包消息内容
            data, target, start_layer, split_layer, reverse_split_layer = msg
            # 计算输出
            data, next_layer, split = calculate_output(model, data, start_layer)
            # 如果不是最后一层
            if split + 1 < model_len:
                # 获取下一个节点的IP
                next_client = config.CLIENTS_LIST[layer_node[split + 1]]
                if next_client not in next_clients:
                    # 添加到发送列表
                    node.connect(next_client, get_client_app_port(next_client, model_name))
                next_clients.append(next_client)
                msg = [info, data.cpu(), target.cpu(), next_layer, split_layer, layer_node]
                node.send_message(node.sock, msg)
                print(
                    f"node_{host_node_num} send msg to node{config.CLIENTS_LIST[layer_node[split + 1]]}"
                )
            else:
                # 到达最后一层，计算损失
                loss = torch.nn.functional.cross_entropy(data, target)
                loss_list.append(loss)
                print("loss :{}".format(sum(loss_list) / len(loss_list)))
                print("")

        # 关闭socket连接
        node_socket.close()
        # 重新初始化节点
        node.__init__(host_ip, host_port)


def get_model(model, type, in_channels, out_channels, kernel_size, start_layer):
    """
     获取当前节点需要计算的模型层

     参数:
     model (nn.Module): 模型
     type (str): 层类型('M':池化层, 'D':全连接层, 'C':卷积层)
     in_channels (int): 输入通道数
     out_channels (int): 输出通道数
     kernel_size (int): 卷积核大小
     start_layer (int): 起始层索引

     返回值:
     features (nn.Sequential): 卷积层和池化层
     dense_s (nn.Sequential): 全连接层
     next_layer (int): 下一层索引
     """
    features = []
    dense_s = []
    if type == "M":
        features.append(model.features[start_layer])
        start_layer += 1
    if type == "D":
        # modify the start_layer
        dense_s.append(model.denses[start_layer-11])
        start_layer += 1
    if type == "C":
        for i in range(3):
            features.append(model.features[start_layer])
            start_layer += 1
    next_layer = start_layer
    return nn.Sequential(*features), nn.Sequential(*dense_s), next_layer


def calculate_output(model, data, start_layer):
    """
    计算当前节点的输出

    参数:
    model (nn.Module): 模型
    data (Tensor): 输入数据
    start_layer (int): 起始层索引

    返回值:
    data (Tensor): 输出数据
    next_layer (int): 下一层索引
    split (int): 当前节点计算的最后一层索引
    """
    output = data
    split = None
    # 初始化next_layer为start_layer
    next_layer = start_layer
    for i, layer_idx in enumerate(split_layer[host_node_num]):
        # TODO:如果节点上的层不相邻，需要兼容
        layer_type = model_cfg[model_name][layer_idx][0]
        in_channels = model_cfg[model_name][layer_idx][1]
        out_channels = model_cfg[model_name][layer_idx][2]
        kernel_size = model_cfg[model_name][layer_idx][3]
        # print("type,in_channels,out_channels,kernel_size",type,in_channels,out_channels,kernel_size)
        features, dense, next_layer = get_model(
            model, layer_type, in_channels, out_channels, kernel_size, start_layer
        )
        if len(features) > 0:
            model_layer = features
        else:
            model_layer = dense
        # 计算输出
        output = model_layer(output)
        # 更新当前节点计算的最后一层索引
        split = layer_idx
    return data, next_layer, split


def start_inference():
    """
    整个推理过程的入口。
    它初始化模型和节点连接，如果包含第一层，它会加载数据集并计算第一层的输出，然后将结果发送给下一个节点。
    最后，它调用 node_inference 函数开始节点推理过程。
    """
    include_first = True
    # 建立连接
    node = NodeEnd(host_ip, host_port)
    model = VGG("Client", model_name, len(model_cfg[model_name]) - 1, model_cfg[model_name])
    model.eval()
    model.load_state_dict(torch.load("models/vgg/vgg.pth"))

    # 如果含第一层，载入数据
    if include_first:
        # TODO:modify the data_dir
        start_layer = 0
        data_dir = "dataset"
        test_dataset = datasets.CIFAR10(
            data_dir,
            train=False,
            transform=transforms.Compose(
                [
                    transforms.ToTensor(),
                    transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
                ]
            ),
            download=True
        )
        test_loader = DataLoader(
            test_dataset, batch_size=256, shuffle=False, num_workers=4
        )

        last_send_ips = []
        for data, target in test_loader:
            # split: 当前节点计算的层
            # next_layer: 下一个权重层
            data, next_layer, split = calculate_output(model, data, start_layer)

            # TODO:modify the port
            next_client = config.CLIENTS_LIST[layer_node[split + 1]]
            if next_client not in last_send_ips:
                node.connect(next_client, get_client_app_port(next_client, model_name))
            last_send_ips.append(next_client)

            # TODO:是否发送labels
            msg = [info, data.cpu(), target.cpu(), next_layer, split_layer, layer_node]
            print(
                f"node{host_node_num} send msg to node{config.CLIENTS_LIST[layer_node[split + 1]]}"
            )
            node.send_message(node.sock, msg)
            include_first = False
            # print('*' * 40)
        node.sock.close()
    node_inference(node, model)


if __name__ == '__main__':

    model_name = "VGG5"
    model_len = len(model_cfg[model_name])

    # 获取所有节点的资源情况
    nodes_resource_infos = get_all_server_info()

    # 根据不同的分割策略
    segmentation_strategy = NetworkSegmentationStrategy(model_name, model_cfg)
    segmentation_points, node_layer_indices = segmentation_strategy.resource_aware_segmentation_points(nodes_resource_infos)
    print('*' * 40)
    print("resource_aware_segmentation_points  segmentation_points: ", segmentation_points)
    print("resource_aware_segmentation_points  node_layer_indices: ", node_layer_indices)

    layer_node = convert_node_layer_indices(node_layer_indices)

    host_port = 9001
    host_node_num = 0
    host_ip = config.CLIENTS_LIST[host_node_num]

    info = "MSG_FROM_NODE(%d), host= %s" % (host_node_num, host_ip)

    loss_list = []

    # 开始推理
    start_inference()
