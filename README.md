# Netlea Aquarium for Home Assistant

尼特利鱼缸灯 Home Assistant 自定义集成。

这个集成面向尼特利 V3 云端设备，重点支持鱼缸灯组的稳定接入、临时开灯、临时关灯和恢复行程。它不是简单把灯当成普通开关处理，而是按鱼缸灯真实使用场景建模：很多用户会配置 24 小时行程，夜间可能是月光模式，但在 Home Assistant 里点击开灯时，预期通常是临时把鱼缸照亮，而不是回到当前行程。

## 当前状态

- 支持短信验证码登录尼特利 V3 云端账号。
- 支持拉取主账号设备和共享给子账号的设备组。
- 支持以“设备组”为单位接入 Home Assistant。
- 支持一个主 `light` 实体。
- 支持三个动作按钮：临时开灯、临时关灯、恢复行程。
- 支持基础状态传感器和行程状态传感器。
- 控制命令使用已验证可工作的云端 WebSocket 控制路径。

## 设计目标

这个项目的目标不是做一个临时脚本，而是做成可以长期使用、可以通过 HACS 自定义仓库安装的 Home Assistant 原生集成。

核心原则：

- **按灯组建模**：一条尼特利云端设备记录对应一个 HA 设备。
- **不拆单灯**：如果尼特利云端返回的是三灯组，HA 里也按一个灯组处理。
- **`turn_on` 语义固定**：永远执行临时开灯。
- **`turn_off` 语义固定**：永远执行临时关灯。
- **恢复行程单独暴露**：通过按钮明确执行，不和 `turn_on` 混用。
- **云端短连接控制**：每次控制时建立 WebSocket，发命令、收回执、断开。
- **REST 轮询状态**：通过尼特利云端同步接口刷新设备状态。

## 支持的实体

添加成功后，每个设备组会创建这些实体。

### Light

- `light.<device>`：鱼缸灯组主实体

行为：

- `turn_on`：临时开灯
- `turn_off`：临时关灯

属性：

- 当前运行模式
- 当前行程
- 控制地址
- 最近一次命令
- 最近一次命令帧
- 最近一次回执数量
- 最近一次回执设备地址列表

### Buttons

- `button.<device>_临时开灯`
- `button.<device>_临时关灯`
- `button.<device>_恢复行程`

保留独立按钮是有意设计。夜间月光模式下，HA 可能已经认为 `light` 是 `on`，这时前端开关按钮未必能再次触发 `turn_on`。独立的“临时开灯”按钮可以强制发送临时开灯命令。

### Sensors

- `sensor.<device>_运行模式`
- `sensor.<device>_当前行程`
- `sensor.<device>_回执灯数`
- `sensor.<device>_在线灯数`
- `sensor.<device>_最后命令`

### Binary Sensors

- `binary_sensor.<device>_有行程`
- `binary_sensor.<device>_行程运行中`

## 控制语义

### 为什么 `turn_on` 不等于恢复行程

鱼缸灯常见配置是全天行程，例如：

- 白天：正常照明
- 夜间：月光模式

如果 `turn_on` 被设计成“有行程就恢复行程”，那夜里点击开灯只会回到月光模式，无法临时照亮鱼缸。

因此本集成采用固定语义：

- `light.turn_on` = 临时开灯
- `light.turn_off` = 临时关灯
- `button.resume_schedule` = 恢复行程

这个设计更符合 Home Assistant 手动控制和自动化调用的直觉。

## 云端协议路径

本集成使用尼特利 V3 云端接口。

### REST

用于登录和设备发现：

- 发送短信验证码
- 验证码登录
- 保存 `token` 和 `user_id`
- 调用设备同步接口拉取设备组

设备发现依赖云端返回的设备列表，因此子账号只要被主账号共享了设备，也可以拉到对应设备组。

### WebSocket

用于发送控制命令：

- 临时开灯
- 临时关灯
- 恢复行程

命令是发给云端返回的组地址。对于多灯组，实际是一次组播命令，回执会按单灯地址分别返回。集成会汇总回执数量和回执地址。

### 已验证的命令族

当前云端控制路径使用已验证有效的旧命令族：

- 临时开灯：`0x01 / 0x1A`
- 临时关灯：`0x01 / 0x1A`
- 恢复行程：`0x01 / 0x10`

没有使用在该云端 WebSocket 控制路径上未验证成功的 `0x06 / 0x20` 命令族。

## 安装

### HACS 自定义仓库

1. 打开 Home Assistant。
2. 进入 HACS。
3. 添加自定义仓库。
4. 仓库类型选择 `Integration`。
5. 填入本仓库地址。
6. 安装 `Netlea Aquarium`。
7. 重启 Home Assistant。

### 手动安装

复制目录：

```text
custom_components/netlea_aquarium
```

到 Home Assistant 配置目录：

```text
config/custom_components/netlea_aquarium
```

然后重启 Home Assistant。

## 配置

1. 打开 Home Assistant。
2. 进入 `设置 -> 设备与服务 -> 添加集成`。
3. 搜索 `Netlea Aquarium` 或 `尼特利鱼缸灯`。
4. 输入尼特利账号手机号。
5. 输入短信验证码。
6. 选择要接入的设备组。

配置成功后，集成会保存：

- 手机号
- 登录 `token`
- `user_id`
- 设备组 ID
- 设备组控制地址
- API 地址
- WebSocket 地址

这些数据保存在 Home Assistant 的配置条目中，不会写入 README 或日志。

## 子账号和共享设备

尼特利设备权限由云端账号决定，不由本地手机决定。

如果主账号把设备共享给子账号，子账号登录后调用设备同步接口时，云端会返回共享设备组。集成不会按“是否本人设备”做额外过滤，只要云端返回设备组，就允许接入。

如果账号只有查看权限或部分控制权限，后续控制可能会被云端拒绝。

## 短信验证码限流

尼特利云端可能限制短信验证码请求频率。

常见情况：

- 手机号格式错误
- 短时间请求过多
- 当天请求次数达到上限
- 云端返回日级流控

如果页面显示发送验证码失败，并且错误详情里出现类似“天级流控”的字样，说明不是集成参数错，而是尼特利云端限制了当天验证码发送次数。需要等待限制恢复，或换可用账号登录。

## 账号互斥

尼特利云端可能存在账号登录互斥。

如果同一个账号在官方 App 或其他设备上重新登录，Home Assistant 里保存的 `token` 可能失效。此时需要在 Home Assistant 中重新认证。

典型表现：

- 设备刷新失败
- 控制失败
- 云端返回账号已在其他设备登录
- 需要重新短信登录

## 目录结构

```text
custom_components/netlea_aquarium/
  __init__.py
  api.py
  binary_sensor.py
  button.py
  config_flow.py
  const.py
  coordinator.py
  entity.py
  light.py
  manifest.json
  sensor.py
  strings.json
  translations/
    zh-Hans.json
```

主要模块：

- `api.py`：尼特利 REST、WebSocket、控制帧和状态解析。
- `config_flow.py`：短信登录、设备发现、设备组选择、重新认证。
- `coordinator.py`：统一刷新云端状态，保存最近一次控制回执。
- `light.py`：主灯组实体。
- `button.py`：临时开灯、临时关灯、恢复行程按钮。
- `sensor.py`：运行模式、当前行程、回执灯数等传感器。
- `binary_sensor.py`：有无行程、行程是否运行。

## 自动化示例

夜间临时开灯：

```yaml
action: light.turn_on
target:
  entity_id: light.your_netlea_aquarium
```

临时关灯：

```yaml
action: light.turn_off
target:
  entity_id: light.your_netlea_aquarium
```

恢复行程：

```yaml
action: button.press
target:
  entity_id: button.your_netlea_aquarium_resume_schedule
```

## 已知边界

- 当前按设备组接入，不拆成单灯实体。
- 当前不实现亮度、色温、RGB 调节。
- 当前不做常驻 WebSocket 推送监听。
- 当前不支持本地 BLE 控制路径。
- 当前状态刷新依赖尼特利云端返回的状态缓存。

这些限制是有意保守处理，优先保证核心控制语义稳定。

## 隐私说明

本集成会与尼特利云端通信。

会发送或保存：

- 尼特利账号手机号
- 短信验证码登录后的 `token`
- 尼特利云端 `user_id`
- 设备组 ID
- 设备组控制地址

不会上传到本仓库：

- 手机号
- `token`
- 本地调试状态文件
- 真实设备地址
- Home Assistant 长期访问令牌

## 开发和校验

基础语法检查：

```bash
python -m py_compile custom_components/netlea_aquarium/*.py
```

JSON 文件检查：

```bash
python -m json.tool hacs.json
python -m json.tool custom_components/netlea_aquarium/manifest.json
python -m json.tool custom_components/netlea_aquarium/strings.json
python -m json.tool custom_components/netlea_aquarium/translations/zh-Hans.json
```

## 免责声明

本项目不是尼特利官方项目，也不隶属于尼特利。  
请自行承担使用第三方 Home Assistant 集成控制设备的风险。
