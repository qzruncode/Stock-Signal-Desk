# 第三方来源与许可证

根目录 [LICENSE](LICENSE) 保留原作者的 MIT 授权声明。本说明不修改任何上游许可证，也不把第三方源码重新授权为 MIT。

本机安装脚本会按固定 revision 获取以下独立服务；下载的 `services/*/app` 源码被 Git 忽略：

| 服务 | 上游 | 本机下载源码中的许可证 |
| --- | --- | --- |
| Firecrawl | https://github.com/firecrawl/firecrawl | GNU AGPL v3 |
| SearXNG | https://github.com/searxng/searxng | GNU AGPL v3 |
| RSSHub | https://github.com/DIYgod/RSSHub | GNU AGPL v3 |

实际使用 revision 以各服务安装脚本为准，授权条款以该 revision 的 `LICENSE` 及相关文件为准。保留第三方授权、署名与源码提供要求；分发修改后的服务或打包整套部署时，需要额外核对这些要求。

Python / npm 依赖遵循各发行包的许可证。项目中的 `patch-package` 补丁只描述对依赖的修改，不替代依赖本身的授权。

证券数据、网页、RSS 内容、PDF 原件和模型权重不因本项目开源获得再分发许可。请按原来源及模型条款使用；仓库不包含用户上传的 PDF、私人配置、行情数据库或模型权重。
