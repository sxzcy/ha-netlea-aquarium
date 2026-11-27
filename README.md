# Netlea Aquarium for Home Assistant (`v2.0.0`)

尼特利鱼缸灯（7 系 90W 等 V3 Wi-Fi/BLE 双模灯具）Home Assistant 自定义集成。

`v2.0.0` 在 100% 兼容 `v1.0.0` 所有实体 `unique_id` 与原有开关语义的基础上，新增 **Local MQTT（内置本地加密服务端，`local_push`）**、**Local BLE（本机蓝牙直连）** 与 **全功能光谱/风扇/氛围灯/日程/离线语音控制**，可完全脱离尼特利云端独立运行。

## 核心特性（v2.0.0）

- **三传输后端可选**：
  - **Local MQTT（推荐，`local_push`）**：集成内置轻量异步 MQTT 3.1.1 服务端（默认监听 `11883`，MAC 白名单认证 + 零填充 AES-128-ECB 加密），配合路由器将灯具出站 `TCP 1883` DNAT 转发至 HA 端口，实现毫秒级双向状态推送，零云端依赖且不占用现有 Mosquitto。
  - **Local BLE**：通过 Home Assistant 蓝牙栈直接连接灯具 GATT，免密钥、免云端、免路由器改配置。
  - **Cloud WebSocket**：完整保留 `v1.0.0` 尼特利 V3 云端 REST + WSS 路径。
- **全功能实体与安全保护**：
  - 主灯支持 **RGBW + 亮度 + 官方 12 种光谱预设（`effect`）**，覆盖 `NL-N7-90-T32/T36` 与 `NL-7S-90-T32/T36`。
  - **Option C 防爆藻控制语义**：
    - `light.turn_on`（无参数）= 临时开灯；`light.turn_off` = 临时关灯；`button.resume_schedule` = 恢复 24 小时日程；
    - 通过 `select.control_mode` 切换「日程自动（`0x1A` 临时控制，默认 60 分钟自动回日程）」与「手动常亮（`0x10` 持久控制）」。
  - **硬件级热与功率保护**：内置官方 `cSpectrums` 功率曲线，强制限制单灯总功率 `<= 91W`，并在功耗 `> 20W` 时强制禁止关闭散热风扇（最低 1 档 PWM `120`）。
  - 独立 **氛围灯**（5 档亮度 + 跟随模式自动退让 `0x22 -> 0x1E`）、**散热风扇（0–10 档真实 PWM 映射）**、**四通道独立滑块**、**离线语音开关（`0x17`）**、**主动状态/日程刷新（`0x15` / `0x25`）**、**RTC 校时（`0x04`）** 与 **Wi-Fi RSSI / 固件版本 / 估算功率** 传感器。
- **零回归平滑升级**：自动将 `v1` 配置项迁移至 `v2`，保留全部原有 `unique_id`（`light`、`temp_on`、`temp_off`、`resume_schedule`、`run_mode`、`current_trip`、`online_count`、`has_travel`、`travel_running` 等），并支持在选项流中无损切换传输模式。

## 支持的实体

| 平台 | 实体 | 说明 |
| --- | --- | --- |
| `light` | 主灯 / 氛围灯 | 主灯 RGBW + Effect；氛围灯 5 档亮度 |
| `select` | 控制模式 / 光谱预设 / 氛围灯跟随模式 | 日程自动 vs 手动常亮；12 种官方预设；氛围灯跟随 |
| `fan` | 散热风扇 | 0–10 档（硬件 PWM `0, 120..250`）含 `>20W` 安全底线 |
| `number` | 临时开灯时长 / 预设档位 / R/W/G/B 通道 | 1–300 分钟、1–20 档、0–100% 通道微调 |
| `button` | 临时开灯 / 临时关灯 / 恢复日程 / 刷新实机状态 / 同步时钟 | 保留原有按钮并新增 `0x15` / `0x04` |
| `switch` | 离线语音 | 控制灯具离线语音开关（`0x17`） |
| `sensor` | 灯光模式 / 当前日程 / 估算功率 / 在线灯数 / Wi-Fi 信号 / 固件版本 / 传输通道 | 含诊断传感器 |
| `binary_sensor` | 已配置日程 / 日程运行中 | 从云端或硬件 `0x25` 本地读取 |

## 服务（Services）

- `netlea_aquarium.set_channels`：直接设置 `R/W/G/B` 与风扇（自动执行 91W 上限与热保护）
- `netlea_aquarium.apply_preset`：应用指定官方光谱预设与档位（1–20 档）
- `netlea_aquarium.query_state`：主动下发 `0x15`（与可选 `0x25` 24 小时日程读取）
- `netlea_aquarium.set_schedule`：通过 `0x12` 写入 1–10 段 24 小时日程（含日出日落缓启）

## 隐私与安全

- 开源仓库不包含任何厂商密钥、手机号、账号 Token 或设备真实地址。
- Local MQTT 的 16 字节 AES 密钥仅保存在用户本机 Home Assistant 配置条目中，并在诊断导出中自动脱敏（`**REDACTED**`）。

## 开发与验证

```bash
python -m unittest discover -s tests -v
```

## 免责声明

本项目不是尼特利官方项目，也不隶属于尼特利。请自行承担使用第三方 Home Assistant 集成控制设备的风险。
