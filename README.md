# COM/TCP/UDP 调试工具

面向 Windows 的本地串口和网络调试工具，支持 COM 串口、TCP 客户端、TCP 服务端、UDP 客户端、UDP 服务端收发测试。软件采用 PySide6/Qt 暗色桌面界面，发布形态为免安装单文件 EXE，目标电脑无需安装 Python 或额外运行环境。

当前版本：`v1.1.8`

## 主要功能

- COM 串口枚举和手动输入，支持波特率、数据位、校验位、停止位、流控、DTR、RTS 设置
- TCP 客户端、TCP 服务端、UDP 客户端、UDP 服务端测试
- 每种网络类型可创建多个连接配置，连接列表按类型分组显示
- 同一时间只允许打开一个连接，打开新连接时会自动关闭当前连接
- TCP 服务端支持多个客户端接入，发送时广播给所有已连接客户端
- UDP 服务端优先回复最近通信的客户端
- 文本发送、16 进制发送、追加 CRLF、自动发送、文件发送
- 自动计算并追加 CRC16-Modbus，追加后的完整数据会写回发送区
- 接收区按“发送时间 / 发送内容 / 接收时间 / 连接 / 接收数据”分列显示
- 接收区长数据自动换行显示，记录行距保持紧凑
- 暂停接收显示、保存接收区、实时保存到文件
- 自动保存配置到 `%APPDATA%\Serial-port-debugging-tools\config.json`
- 支持配置导入、导出和清除
- 显示发送/接收字节计数和每秒收发速度
- 使用 `assets/app.png` 作为窗口和 EXE 图标

## 界面说明

- 左侧是连接管理区，包含连接列表、串口或网络参数、连接操作和计数。
- 右侧是工作区，上半部分为发送区，下半部分为接收区。
- 发送区不再显示单独发送历史，主要空间用于编辑发送内容。
- 接收区会把下一条接收数据和最近一次发送内容对应显示；主动上报数据没有对应发送时，发送列为空。
- 接收区使用暗色表格显示历史记录，支持选择和右键复制。
- 发送内容输入框支持复制、剪切、粘贴和多行文本。
- 连接状态会在连接列表和底部状态栏同步显示，底部状态栏的连接地址只显示一次。
- 窗口内容区不重复显示程序名称和版本号，应用标题保留在系统标题栏中。

## 直接使用 EXE

打包后的程序位于：

```text
dist\COM串口调试工具.exe
```

双击即可运行。该 EXE 是单文件程序，目标电脑不需要安装 Python、PySide6、pyserial 或其它运行环境。

## 从源码运行

开发环境需要安装 Python 3.10 或更高版本。

```powershell
python -m pip install -r requirements.txt
python main.py
```

也可以在安装依赖后双击：

```text
start.bat
```

## 打包 EXE

推荐直接双击：

```text
build_exe.bat
```

脚本会安装打包依赖、从 `assets\app.png` 生成临时 ICO、删除旧 EXE，然后重新生成单文件程序：

```text
dist\COM串口调试工具.exe
```

也可以手动执行：

```powershell
python -m pip install -r requirements.txt pyinstaller pillow
python tools\make_icon.py
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "COM串口调试工具" --icon "assets\app.generated.ico" --add-data "assets\app.png;assets" --hidden-import serial.tools.list_ports_windows main.py
```

`assets\app.generated.ico` 是打包时生成的临时文件，不需要提交。

## 连接规则

选中 `COM串口` 时不需要创建连接，选择串口参数后直接点击 `打开串口`。

选中 TCP/UDP 类型时，先填写参数并点击 `创建连接`，连接配置会显示在对应类型下面。只有选中某个已创建的连接并点击 `打开连接` 后，程序才会真正连接或监听。

网络参数规则：

- `TCP客户端`：填写目标 IP、目标端口，本地端口随机分配
- `UDP客户端`：填写目标 IP、目标端口，本地端口随机分配
- `TCP服务端`：填写本地端口
- `UDP服务端`：填写本地端口

连接名称只显示端点信息，例如：

```text
10.10.1.57:10123
本机:665
```

## 16 进制发送

发送区默认按文本发送。勾选发送区 `16进制` 后，可以使用以下格式：

```text
4E 57 00 13
4E,57,00,13
0x4E 0x57 0x00 0x13
```

接收区默认按文本显示。勾选接收区 `16进制` 后，接收到的数据会按字节显示。

## 自动 CRC

发送区勾选 `自动CRC` 后，点击 `发送` 时会自动计算 CRC16-Modbus，并把 CRC 低字节在前追加到实际发送的数据末尾。追加后的完整指令会写回发送区，方便确认和再次发送。

如果指令末尾已经带有正确的 CRC16-Modbus，程序会直接发送原数据，不会重复追加。

示例：

```text
发送区输入：01 03 00 00 00 0A
实际发送：01 03 00 00 00 0A C5 CD
```

自动 CRC 同时适用于手动发送、自动发送和文件发送。

## 配置保存

程序会在启动时自动读取配置，并在修改串口参数、网络参数、发送/接收选项或连接列表后自动保存。

配置文件固定保存在：

```text
%APPDATA%\Serial-port-debugging-tools\config.json
```

菜单 `配置(C)` 提供：

- `导入配置`：从 JSON 文件导入配置，并同步保存到 `%APPDATA%`
- `导出配置`：把当前配置导出为 JSON 文件
- `清除配置`：删除已保存配置并恢复默认设置

## 开发检查

提交前运行：

```powershell
python -m py_compile main.py qt_app.py tools\make_icon.py
python -m unittest discover -s tests
```

## 项目结构

```text
.
├─ main.py                  # 常量、通信逻辑兼容入口和 Qt 启动入口
├─ qt_app.py                # PySide6 暗色界面和桌面事件绑定
├─ requirements.txt         # 源码运行依赖
├─ start.bat                # 源码启动脚本
├─ build_exe.bat            # EXE 打包脚本
├─ assets\
│  └─ app.png               # 应用图标源文件
├─ tools\
│  └─ make_icon.py          # PNG 转 ICO 工具
├─ tests\
│  └─ test_payload.py       # 基础单元测试
└─ dist\                   # 本地打包输出目录，不提交 release 文件
```

## 常见问题

### 源码运行提示缺少依赖

先安装依赖：

```powershell
python -m pip install -r requirements.txt
```

直接运行 `dist\COM串口调试工具.exe` 不需要安装依赖。

### 找不到 COM 串口

确认设备驱动已经安装，并在 Windows 设备管理器中能看到对应的 COM 端口。也可以在串口下拉框中手动输入端口号，例如 `COM3`。

### TCP 服务端无法监听端口

检查端口是否已被其它程序占用，或是否被安全软件、防火墙策略拦截。服务端模式只需要填写本地端口。

### UDP 服务端发送失败

UDP 服务端会优先回复最近一次发来数据的客户端。请先让客户端向服务端发送一帧数据，再从服务端发送回复。
