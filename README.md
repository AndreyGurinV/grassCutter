# grassCutter — тележка на 4× BLDC + Raspberry Pi

Управление четырьмя контроллерами **JRAHK Double Row 18 Tubes** с **Raspberry Pi Model B Rev 2**.

- Скорость: аналог **0–5 В** через **MCP4728** (I2C)
- Реверс: сухой контакт через **PC817**
- Кинематика: **skid-steer / tank**
- E-STOP: контактор на BAT+

## Документация

| Файл | Содержание |
|---|---|
| [docs/harness.md](docs/harness.md) | Распиновка throttle/reverse, чеклист сверки |
| [docs/control_board.md](docs/control_board.md) | Схема платы, BOM, GPIO |
| [docs/power.md](docs/power.md) | Батарея, контактор, buck 5 В |
| [docs/JRAHK_Full_Catalog -1.pdf](docs/JRAHK_Full_Catalog%20-1.pdf) | Каталог контроллеров |

## Быстрый старт (на Pi)

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv i2c-tools
# включить I2C: sudo raspi-config → Interface → I2C
git clone <repo> && cd grassCutter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
# отредактировать калибровку throttle и пины при необходимости
i2cdetect -y 1
python -m grasscutter.cli status
python -m grasscutter.cli arm
python -m grasscutter.cli drive --v 0.1 --w 0
python -m grasscutter.cli stop
python -m grasscutter.cli disarm
```

Без железа (мок):

```bash
GRASSCUTTER_MOCK=1 python -m grasscutter.cli drive --v 0.2 --w 0.1
```

## Карта GPIO (по умолчанию)

| Сигнал | BCM |
|---|---|
| SDA / SCL | 2 / 3 |
| DIR FL / FR / RL / RR | 17 / 27 / 22 / 23 |
| ESTOP enable | 24 |
| Status LED | 25 |

## Безопасность

Колёса в воздухе на первом прогоне. Не реверсить под тягой. Контактор по умолчанию выключен.
