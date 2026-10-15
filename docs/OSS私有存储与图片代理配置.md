# OSS 私有存储与图片代理配置

当前采用用户在 2026-10-14 确认的临时方案：原图、阅读图和缩略图存入私有 OSS，网页通过项目后端上传和读取。暂不绑定 OSS 自定义域名。

## 已有资源与访问路径

- Bucket：`ai-notes-prod-20261014`。
- 地域：杭州，`cn-hangzhou`。
- 保持私有权限和阻止公共访问；已有版本控制保持开启。
- 域名：`sljx-hub.top`，暂未备案；当前本地开发入口为 `http://localhost:5173`。

上传：浏览器 → 登录与 CSRF 校验 → API → OSS。

阅读：浏览器 → 登录、所有者与短期凭证校验 → API → OSS → 返回图片内容。

浏览器收到项目自己的 `/api/v1/files/.../content` 地址，后端不会转发 OSS 的强制下载响应头。每次图片读取都重新检查会话和笔记所有权，管理员也不能绕过。短期凭证默认有效 300 秒，网页可重新加载获取新凭证。

这一模式不需要 OSS CNAME（域名别名）或图片域名证书，也不依赖浏览器到 OSS 的 CORS（跨域资源共享）。之前添加的跨域规则可以保留。公网网站入口与备案单独处理；这里先完成本地功能与 OSS 接入。

## 下一步：创建受限 RAM 用户

在阿里云控制台进入 **RAM 访问控制 → 用户 → 创建用户**：

先确认当前主账号的 OSS Bucket 列表中能看到 `ai-notes-prod-20261014`，然后在这个账号创建 RAM 用户与策略。ECS 可以属于另一账号；项目通过 Bucket 所属账号的受限凭据访问存储。此次首次联调的 403 原因是 RAM 用户创建在另一个账号，纠正账号后已通过。

1. 用户名可以填 `ai-notes-storage`；选择用于程序调用的访问方式。无需开通控制台登录权限。
2. 在权限策略中创建自定义策略，例如 `ai-notes-storage-prefix`，切换到 JSON 编辑并使用下方内容。
3. 将该策略授予这个 RAM 用户。不要额外授予 `AliyunOSSFullAccess` 或管理员权限。
4. 按控制台流程为该用户创建 AccessKey（访问密钥），只保存在忽略的本地配置或服务器私密配置中，不发到聊天、截图或 GitHub。

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["oss:GetObject", "oss:PutObject", "oss:DeleteObject"],
      "Resource": ["acs:oss:*:*:ai-notes-prod-20261014/ai-notes/*"]
    }
  ]
}
```

权限只覆盖本项目对象前缀；`HeadObject` 使用读取对象权限。当前实现不需要列举 Bucket、修改 Bucket ACL、删除历史版本或管理账户权限。将来使用 ECS RAM 角色与自动轮转凭据时再替换当前配置方式。

## 配置 API 与 Worker

参考根目录 `.env.example` 的变量名，实际值仅写入 Git 忽略的运行配置。API 与 Worker 必须使用相同数据库和 OSS 配置：

```dotenv
STORAGE_BACKEND=oss
OSS_REGION=cn-hangzhou
OSS_BUCKET=ai-notes-prod-20261014
OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
OSS_PREFIX=ai-notes/
OSS_ACCESS_KEY_ID=<仅在本机或服务器填写>
OSS_ACCESS_KEY_SECRET=<仅在本机或服务器填写>
OSS_SECURITY_TOKEN=
```

本地电脑使用以上公网 Endpoint（服务访问地址）。杭州 ECS 可改为 `https://oss-cn-hangzhou-internal.aliyuncs.com`，前提是确认内网可达；电脑不要用内网地址。Endpoint 填区域地址，不填 Bucket 完整域名或自己的域名。

`Settings` 从进程环境和当前工作目录的 `.env` 读取。原生启动时工作目录为 `backend`，因此私密文件应放在 `backend/.env`，或者通过进程环境注入；Compose 使用根目录 `.env`。不要把两处真实凭据复制进示例文件。

配置生效需要重启 API 和 Worker。`APP_ENV=production` 还要求 HTTPS 的 `APP_ORIGIN` 和 `COOKIE_SECURE=true`，且缺少 OSS 配置会拒绝启动。本地联调保留 `APP_ENV=development`。

默认单张上限 20 MiB、4000 万像素、每批 50 张、每人原图配额 1 GiB。仅支持有效 JPEG、PNG、WebP；Worker 拒绝动画和伪造图片，保留原图并生成阅读图与缩略图。派生图、历史版本和请求费用不包含在应用原图配额中。

## 联调、故障与回退

配置完成后，先确认一次小规模真实 OSS 联调的调用范围与费用，再上传两张测试图片。检查后台处理完成、两张图片可读和页序可调整；使用另一个账户确认无法读取；重启进程后再次打开同一笔记。

网络或权限失败时 API 返回脱敏错误；后台失败重试耗尽后显示“处理失败”，可由所有者手动重试。排查时只分享错误代码与 `request_id`，不要分享密钥、Cookie 或带短期凭证的图片 URL。

开发阶段可将 `STORAGE_BACKEND` 改回 `local`，并让 API 与 Worker 使用同一个绝对 `STORAGE_ROOT`。本地 Compose 已为它们配置共享 `filesdata` 卷。切换后不会自动搬迁原图；已有 OSS 图片仍需恢复 OSS 配置才能读取，不能把切换当作数据迁移。

Bucket 版本控制已启用，删除对象可能只产生删除标记，历史对象继续计费。生命周期策略与备份保留时间应单独确认后配置。Worker 在部分上传或进程崩溃后产生的孤儿派生对象清理仍待补齐，因此本阶段不声明存储生命周期与完整 M1 已验收。

## 当前验证边界

官方 SDK 配置、私有上传/处理/代理读取、所有者权限、凭证过期、失败重试与响应头隔离已通过隔离测试。SDK 回归测试使用替身客户端。用户纠正 RAM 账号、配置新凭据与授权后，真实 Bucket 的 77 字节合成图片上传、大小检查、读取比对、删除和删除后不存在检查已通过；历史版本未清除。

用户另行批准“网页联调”后，两张生成 PNG 的本机网页上传、Worker 读取原图并生成 4 张派生图、同源图片阅读、缩放、页序调整和刷新保留均已验证。两个任务各尝试一次即完成，原图 SHA256 与本地测试图一致。测试笔记保留供用户查看。真实手机触控、跨用户云端实机与 ECS 部署仍未验收。详见 `task-evidence/2026-10-14-oss-private-proxy.md`。

参考：[OSS SDK V2 上传](https://help.aliyun.com/zh/oss/developer-reference/simple-upload-using-oss-sdk-for-python-v2)、[下载](https://help.aliyun.com/zh/oss/developer-reference/simple-download-using-oss-sdk-for-python-v2)、[RAM 权限策略](https://help.aliyun.com/zh/oss/user-guide/ram-policy-overview)。
