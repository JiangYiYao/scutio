# 第三方说明

Scutio 自有代码采用本目录的 [MIT 许可证](LICENSE)。以下记录当前分发内容的来源；第三方部分保留各自的版权与许可声明。

## 运行时依赖

[AKShare](https://github.com/akfamily/akshare) 通过包管理器安装，当前固定版本的许可证为 MIT，版权声明为 Copyright (c) 2019-2026 Albert King。Scutio 调用其选定接口，不在仓库中复制其适配器源码。

完整的直接运行时依赖及版本约束见 [requirements.txt](requirements.txt)。AKShare、requests、pandas 等依赖及其传递依赖的许可声明由各自发行包携带；本项目的 MIT 许可证不替代它们。

## 接口与数据来源

Financial API 按 [HiThink-Tech 官方接口文档](https://github.com/HiThink-Tech/Financial-API/tree/main/docs/api) 通过 HTTP 接入，未引入官方 SDK 源码。研报列表与 PDF 使用东财接口，分页、检索与文件处理由项目内模块负责。其他直接数据适配的范围见 [数据服务说明](references/toolkit/12-data-sources.md)。

源站数据、新闻、研报与公告内容不因 Scutio 的代码许可证而获得 MIT 授权，其使用条件由相应提供方规定。
