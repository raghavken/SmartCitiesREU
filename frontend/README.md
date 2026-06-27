# Arduino Dual Motor PWM Serial GUI

This project provides a Python-served dashboard for controlling an Arduino Uno
dual-motor controller over a selected serial port.

## Run

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python labview_serial_gui.py
```

The app prints a local URL and opens the browser automatically. To keep it in
the terminal only:

```bash
.venv/bin/python labview_serial_gui.py --no-browser --port 8765
```

## Front Panel Logic

The GUI writes newline-terminated PWM commands every loop interval.

Current command format:

```text
electric_motor_pwm=<0-255>,internal_combustion_engine_pwm=<0-255>\n
```

Example command:

```text
electric_motor_pwm=128,internal_combustion_engine_pwm=64\n
```

The app reads one Arduino reply line at a time and displays it in the serial
monitor. Replies can be plain text; they do not need to be numeric. Lines with
`rpm=<number>`, `battery_voltage=<number>`, or
`motor_output_voltage=<number>` are also shown in the telemetry panel. The raw
ADC counts can also be sent as `battery_voltage_value=<number>` and
`motor_output_voltage_value=<number>`.

Example Arduino response:

```text
ok electric_motor_pwm=128 internal_combustion_engine_pwm=64
```

Example telemetry response:

```text
telemetry rpm=1234.5 battery_voltage_value=612 battery_voltage=9.24 motor_output_voltage_value=488 motor_output_voltage=7.37
```

Default serial settings:

- Baud rate: `115200`
- Loop interval: `20` ms
- GUI sample display interval: `1000` ms
- Serial timeout: `500` ms
- Line ending: `\n`
