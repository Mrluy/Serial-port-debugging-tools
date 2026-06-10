# 本地COM串口调试工具

一个参考 TCP&UDP 测试工具布局写的本地 COM 串口调试工具，适合在 Windows 上做串口收发、协议帧调试和日志保存。

## 功能

- 枚举本机 COM 串口
- 配置波特率、数据位、校验位、停止位、流控、DTR/RTS
- 文本发送和 16 进制发送
- 自动循环发送，支持发送间隔
- 文件发送
- 文本接收和 16 进制接收
- 暂停显示、时间戳、接收区保存、实时保存到文件
- 发送/接收字节计数和每秒速度显示

## 运行

```powershell
python -m pip install -r requirements.txt
python main.py
```

也可以在安装依赖后双击 `start.bat` 启动。

如果程序能打开但提示缺少 `pyserial`，说明还没有安装依赖，执行上面的第一条命令即可。

## 打包成 exe

可选步骤，双击 `build_exe.bat`，或手动执行：

```powershell
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "COM串口调试工具" --hidden-import serial.tools.list_ports_windows main.py
```

打包结果是 `dist\COM串口调试工具.exe`，目标电脑无需安装 Python 或 pyserial。

## 16 进制格式

发送区支持以下格式：

```text
4E 57 00 13
4E,57,00,13
0x4E 0x57 0x00 0x13
```

开启 `16进制` 发送时，程序会按字节发送；关闭时会按选择的编码发送文本。
