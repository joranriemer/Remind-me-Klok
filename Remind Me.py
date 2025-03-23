import sounddevice as sd
import numpy as np
import noisereduce as nr
import webrtcvad
import json
import sqlite3
import subprocess
import time
import re
from vosk import Model, KaldiRecognizer
import RPi.GPIO as GPIO
from time import sleep
import smbus2 as smbus
import threading
from datetime import datetime
import asyncio
from bleak import BleakClient

# Schakel GPIO-waarschuwingen uit
GPIO.setwarnings(False)
GPIO.cleanup()

# GPIO-instellingen
BUZZER_PIN = 27
LAMPJE_PIN = 22
KNOP_PIN = 17  # Knop pin
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.setup(LAMPJE_PIN, GPIO.OUT)
GPIO.setup(KNOP_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# I2C-instellingen voor LCD
I2C_ADDR = 0x27
bus = smbus.SMBus(1)
LCD_WIDTH = 16
LCD_CMD = 0
LCD_CHR = 1
LCD_BACKLIGHT = 0x08
ENABLE = 0b00000100
LCD_LINES = [0x80, 0xC0, 0x94, 0xD4]
E_DELAY = 0.001
E_PULSE = 0.0005

# Database-verbinding
DB_PATH = "herinneringen.db"
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Globale flags
stop_flag = False
herinnering_actief = False
buzzer_actief = False
lampje_actief = False
knop_ingedrukt_count = 0  # Teller voor het aantal keren dat de knop is ingedrukt

# BLE-instellingen
XIAO_MAC_ADDRESS = "FA:91:CC:45:26:5B"
RX_CHARACTERISTIC_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
ble_client = None  # Globale BLE-client

# Timer voor herinnering (2 minuten)
HERINNERING_DUUR = 60  # 120 seconden (2 minuten)

# Vosk Model (zorg dat het model al gedownload is!)
model = None

def get_model():
    global model
    if model is None:
        print("📥 Loading VOSK model...")
        model = Model("/home/pioneers/vosk_models/vosk-model-small-en-us-0.15")
    return model

recognizer = KaldiRecognizer(get_model(), 16000)

RATE = 16000   # Stel de sample rate in op 16000
DURATION = 5   # Stel de duur in op 5 seconden

# Opname-functie
def record_audio(duration=DURATION, samplerate=RATE):
    audio = sd.rec(int(duration * samplerate), samplerate=samplerate, channels=1, dtype='int16', device=2)
    sd.wait()
    print("✅ Opname klaar.")
    return np.squeeze(audio)

def speech_to_text(audio):
    print("📝 Converteert spraak naar tekst...")
    recognizer.AcceptWaveform(audio.tobytes())
    result = json.loads(recognizer.Result())
    return result.get("text", "")

def apply_noise_reduction(audio, samplerate=RATE):
    print("🔇 Verwijdert ruis...")
    return nr.reduce_noise(y=audio.astype(np.float32), sr=samplerate, prop_decrease=0.9).astype(np.int16)

def vad_filter(audio, samplerate=RATE):
    print("🛑 Filtert stiltes en ruis...")
    vad = webrtcvad.Vad(3)
    frame_size = int(samplerate * 0.03)
    return np.array([audio[i:i + frame_size] for i in range(0, len(audio) - frame_size, frame_size) if vad.is_speech(audio[i:i + frame_size].tobytes(), samplerate)]).flatten()

def capture_speech():
    raw_audio = record_audio()
    if raw_audio is None:
        return None
    clean_audio = apply_noise_reduction(raw_audio)
    speech_audio = vad_filter(clean_audio)
    recognized_text = speech_to_text(speech_audio)

    if recognized_text:
        print(f"🗣 Herkende tekst: {recognized_text}")
        return recognized_text
    else:
        print("❌ Geen spraak gedetecteerd. Probeer opnieuw.")
        return None

def buzzer_beep():
    """Laat de buzzer een korte piep geven."""
    GPIO.output(BUZZER_PIN, GPIO.HIGH)
    sleep(0.2)  # Piep voor 0.2 seconden
    GPIO.output(BUZZER_PIN, GPIO.LOW)

def activate_virtualenv():
    print("Activating virtual environment...")
    try:
        subprocess.run("bash -i -c 'source mijn_venv/bin/activate'", shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print("Virtual environment activated.")
    except subprocess.CalledProcessError as e:
        print(f"Error activating virtual environment: {e}")

def run_deepseek():
    """Start Deepseek proces, dit is nu in de achtergrond om de snelheid van spraakherkenning te verhogen."""
    try:
        process = subprocess.Popen(
            ["ollama", "run", "deepseek-r1:1.5b"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print("Deepseek is starting.")
        return process
    except Exception as e:
        print(f"Error starting Deepseek: {e}")
        return None

def format_time(time_str):
    """Zorgt dat tijd altijd in HH:MM formaat is."""
    if re.match(r"^\d{1}:\d{2}$", time_str):
        return f"0{time_str}"
    return time_str

def filter_text(text):
    """Haalt beschrijving, datum en tijd uit de AI-output."""
    match = re.search(r"\(\s*(.*?)\s*,\s*(\d{4}-\d{2}-\d{2})\s*,\s*(\d{1,2}:\d{2})\s*\)", text)
    if match:
        beschrijving, datum, tijd = match.groups()
        tijd = format_time(tijd)
        return beschrijving, datum, tijd
    return None

def voeg_herinnering_toe(beschrijving, datum, tijd):
    """Voegt een herinnering toe aan de database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS herinneringen (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        beschrijving TEXT,
                        datum TEXT,
                        tijd TEXT)''')

        c.execute("INSERT INTO herinneringen (beschrijving, datum, tijd) VALUES (?, ?, ?)",
                  (beschrijving, datum, tijd))
        conn.commit()
        conn.close()
        print(f"✅ Herinnering opgeslagen: {beschrijving} op {datum} om {tijd}.")
        return True
    except Exception as e:
        print(f"❌ Fout bij opslaan in database: {e}")
        return False

def send_to_deepseek(process, user_input):
    """Stuurt de tekst naar Deepseek voor extractie."""
    if process is None:
        print("Deepseek process is not running. Exiting.")
        return None

    text = f"""
    Extract the following information from this text:

    Text: "{user_input}"

    Format the output exactly like this:
    (DESCRIPTION, DATE, TIME)

    DESCRIPTION: The main task in 1-4 essential words (do not include time or date).
    DATE: In YYYY-MM-DD format. (YYYY is always 2025)
    TIME: In 24-hour HH:MM format. (Never include letters)
    """

    try:
        print("🧠 Verstuurt data naar Deepseek...")
        process.stdin.write(text + "\n")
        process.stdin.flush()

        stdout, stderr = process.communicate()

        if process.returncode == 0:
            print("Deepseek output ontvangen:")
            print(stdout)
            resultaat = filter_text(stdout)
            if resultaat:
                return resultaat
        print("❌ Fout in Deepseek-output.")
        return None
    except Exception as e:
        print(f"Error sending data to Deepseek: {e}")
        return None

# LCD-functies
def lcd_init():
    commands = [0x33, 0x32, 0x06, 0x0C, 0x28, 0x01]
    for cmd in commands:
        lcd_byte(cmd, LCD_CMD)
        time.sleep(E_DELAY)

def lcd_byte(data, mode):
    high = mode | (data & 0xF0) | LCD_BACKLIGHT
    low = mode | ((data << 4) & 0xF0) | LCD_BACKLIGHT
    for byte in [high, low]:
        bus.write_byte(I2C_ADDR, byte)
        lcd_toggle_enable(byte)

def lcd_toggle_enable(data):
    time.sleep(E_DELAY)
    bus.write_byte(I2C_ADDR, data | ENABLE)
    time.sleep(E_PULSE)
    bus.write_byte(I2C_ADDR, data & ~ENABLE)
    time.sleep(E_DELAY)

def lcd_display(lines):
    for i, text in enumerate(lines[:len(LCD_LINES)]):
        lcd_byte(LCD_LINES[i], LCD_CMD)
        for char in text.ljust(LCD_WIDTH):
            lcd_byte(ord(char), LCD_CHR)

def lcd_scroll_text(line, text, delay=1, pause=2):
    lcd_byte(LCD_LINES[line], LCD_CMD)
    text = text.ljust(LCD_WIDTH)
    lcd_display_line(line, text)
    time.sleep(pause)
    for i in range(len(text) - LCD_WIDTH + 1):
        segment = text[i:i + LCD_WIDTH]
        lcd_display_line(line, segment)
        time.sleep(delay)

def lcd_display_line(line, text):
    lcd_byte(LCD_LINES[line], LCD_CMD)
    for char in text.ljust(LCD_WIDTH):
        lcd_byte(ord(char), LCD_CHR)

def lcd_clear():
    lcd_byte(0x01, LCD_CMD)

# Database-functies
def haal_herinneringen_op(huidige_datum, huidige_tijd):
    c.execute("SELECT beschrijving FROM herinneringen WHERE datum=? AND tijd=?", (huidige_datum, huidige_tijd))
    return c.fetchall()

def verplaats_herinnering_naar_verlopen(beschrijving, datum, tijd):
    conn_verlopen = sqlite3.connect('verlopen_herinneringen.db')
    c_verlopen = conn_verlopen.cursor()

    c_verlopen.execute('''CREATE TABLE IF NOT EXISTS verlopen_herinneringen (
        beschrijving TEXT, datum TEXT, tijd TEXT)''')
    conn_verlopen.commit()

    c_verlopen.execute("INSERT INTO verlopen_herinneringen (beschrijving, datum, tijd) VALUES (?, ?, ?)",
                       (beschrijving, datum, tijd))

    conn_verlopen.commit()
    conn_verlopen.close()

# Buzzer- en lampje-functies
def start_buzzer_en_lampje():
    global buzzer_actief, lampje_actief
    buzzer_actief = True
    lampje_actief = True
    while buzzer_actief and lampje_actief:
        GPIO.output(BUZZER_PIN, GPIO.HIGH)
        GPIO.output(LAMPJE_PIN, GPIO.HIGH)
        time.sleep(0.2)
        GPIO.output(BUZZER_PIN, GPIO.LOW)
        GPIO.output(LAMPJE_PIN, GPIO.LOW)
        time.sleep(0.2)

def stop_buzzer_en_lampje():
    global buzzer_actief, lampje_actief
    buzzer_actief = False
    lampje_actief = False
    GPIO.output(BUZZER_PIN, GPIO.LOW)
    GPIO.output(LAMPJE_PIN, GPIO.LOW)

def korte_buzz():
    GPIO.output(BUZZER_PIN, GPIO.HIGH)
    time.sleep(0.1)
    GPIO.output(BUZZER_PIN, GPIO.LOW)

# BLE-functies
async def control_led(action):
    global ble_client
    try:
        if ble_client is None or not ble_client.is_connected:
            ble_client = BleakClient(XIAO_MAC_ADDRESS)
            await ble_client.connect()
            print("Verbonden met XIAO")

        if action == "blink":
            await ble_client.write_gatt_char(RX_CHARACTERISTIC_UUID, b'2')
            print("XIAO LED KNIPPEREN")
        elif action == "off":
            await ble_client.write_gatt_char(RX_CHARACTERISTIC_UUID, b'0')
            print("XIAO LED UIT")
            # Verbreek de verbinding na het uitschakelen van de LED
            await ble_client.disconnect()
            ble_client = None
            print("BLE-verbinding gesloten")
    except Exception as e:
        print(f"BLE Fout: {e}")

async def main():
    global stop_flag, herinnering_actief, knop_ingedrukt_count, ble_client
    lcd_init()

    # Start Deepseek proces aan het begin van de loop
    deepseek_process = run_deepseek()
    if not deepseek_process:
        return

    vorige_tijd = ""
    laatste_opruiming = time.time()

    while not stop_flag:
        # Controleer knop
        if GPIO.input(KNOP_PIN) == GPIO.LOW:  # Knop is ingedrukt
            print("Knop ingedrukt!")
            knop_ingedrukt_count += 1

            # Eerste klik: stop buzzer, lampje en XIAO LED, maar blijf herinnering tonen
            if knop_ingedrukt_count == 1 and herinnering_actief:
                stop_buzzer_en_lampje()
                korte_buzz()  # Korte buzz na eerste druk
                # Zet de LED van XIAO uit
                await control_led("off")
                print("Buzzer en LED gestopt, herinnering blijft tonen.")
                time.sleep(0.5)  # Voorkom dubbele knopdrukken door debounce

            # Tweede klik: stop herinnering en keer terug naar klokfunctie
            elif knop_ingedrukt_count == 2 and herinnering_actief:
                stop_buzzer_en_lampje()
                korte_buzz()  # Korte buzz na tweede druk
                herinnering_actief = False
                lcd_clear()  # LCD leegmaken voordat de klokmodus wordt weergegeven
                lcd_display([f"Tijd: {datetime.now().strftime('%H:%M')}", f"Datum: {datetime.now().strftime('%Y-%m-%d')}"])
                knop_ingedrukt_count = 0  # Reset knopdrukken
                print("Herinnering gestopt, terug naar klok.")
                time.sleep(0.5)  # Voorkom dubbele knopdrukken door debounce

            # Als de knop wordt ingedrukt zonder actieve herinnering, start spraakopname
            elif not herinnering_actief:
                print("🎤 Knop ingedrukt! Start met praten")
                buzzer_beep()  # 🔊 Buzzer piept vóór de opname
                start_time = time.time()  # Tijd bijhouden voor performance

                text = capture_speech()
                if not text:
                    continue

                print("🎬 Opname voltooid! Start Deepseek...")
                buzzer_beep()  # 🔊 Buzzer piept na de opname

                # Verstuur de tekst naar Deepseek
                ai_resultaat = send_to_deepseek(deepseek_process, text)
                if ai_resultaat:
                    beschrijving, datum, tijd = ai_resultaat
                    if voeg_herinnering_toe(beschrijving, datum, tijd):
                        end_time = time.time()
                        print(f"✅ Proces voltooid in {end_time - start_time:.2f} seconden.")
                        continue

                print("❌ Probeer opnieuw met dezelfde tekst.")

        huidige_datum = datetime.now().strftime("%Y-%m-%d")
        huidige_tijd = datetime.now().strftime("%H:%M")

        # Opruimen verlopen herinneringen (elke minuut)
        if time.time() - laatste_opruiming > 60:
            verplaats_verlopen_herinneringen()
            laatste_opruiming = time.time()

        herinneringen = haal_herinneringen_op(huidige_datum, huidige_tijd)

        if herinneringen and not herinnering_actief:
            herinnering_actief = True  # Markeer dat een herinnering bezig is
            beschrijving = herinneringen[0][0]  # Eerste herinnering tonen
            start_tijd = time.time()  # Starttijd van de herinnering

            print(f"Herinnering: {beschrijving}")
            lcd_clear()  # LCD leegmaken voordat de herinnering wordt weergegeven
            lcd_display(["Herinnering:", ""])
            time.sleep(1)

            # Verplaats de herinnering naar de verlopen tabel
            verplaats_herinnering_naar_verlopen(beschrijving, huidige_datum, huidige_tijd)
            # Verwijder de herinnering uit de actieve tabel
            c.execute("DELETE FROM herinneringen WHERE beschrijving = ? AND datum = ? AND tijd = ?",
                      (beschrijving, huidige_datum, huidige_tijd))
            conn.commit()

            # Start piepen, lampje en BLE LED in aparte thread
            buzzer_lampje_thread = threading.Thread(target=start_buzzer_en_lampje)
            buzzer_lampje_thread.start()

            # Start BLE LED knipperen
            await control_led("blink")

            # Herinnering tonen in aparte thread om de knopcontrole niet te blokkeren
            herinnering_thread = threading.Thread(target=toon_herinnering, args=(beschrijving, start_tijd))
            herinnering_thread.start()

        else:
            if huidige_tijd != vorige_tijd:  # Alleen updaten als de tijd verandert
                lcd_display([f"Tijd:{huidige_tijd}", f"Datum:{huidige_datum}"])
                vorige_tijd = huidige_tijd

            time.sleep(1)

    # Sluit de BLE-verbinding bij het afsluiten van het programma
    if ble_client and ble_client.is_connected:
        await ble_client.disconnect()
        print("BLE-verbinding gesloten")

    lcd_clear()
    GPIO.cleanup()
    print("Programma gestopt.")

def toon_herinnering(beschrijving, start_tijd):
    global herinnering_actief
    while herinnering_actief and (time.time() - start_tijd < HERINNERING_DUUR):
        if len(beschrijving) > LCD_WIDTH:
            lcd_scroll_text(1, beschrijving)
        else:
            lcd_display(["Herinnering:", beschrijving])
        time.sleep(1)  # Kortere sleep voor betere responsiviteit

    # Stop de herinnering na 2 minuten
    herinnering_actief = False
    stop_buzzer_en_lampje()
    asyncio.run(control_led("off"))
    lcd_clear()
    lcd_display(["Herinnering", "gestopt"])
    time.sleep(0.5)

# Verplaatsen verlopen herinneringen
def verplaats_verlopen_herinneringen():
    conn_actief = sqlite3.connect('herinneringen.db')
    c_actief = conn_actief.cursor()

    conn_verlopen = sqlite3.connect('verlopen_herinneringen.db')
    c_verlopen = conn_verlopen.cursor()

    c_verlopen.execute('''CREATE TABLE IF NOT EXISTS verlopen_herinneringen (
        beschrijving TEXT, datum TEXT, tijd TEXT)''')
    conn_verlopen.commit()

    huidige_datum = datetime.now().strftime("%Y-%m-%d")
    huidige_tijd = datetime.now().strftime("%H:%M")

    c_actief.execute("SELECT * FROM herinneringen WHERE datum < ? OR (datum = ? AND tijd < ?)",
                     (huidige_datum, huidige_datum, huidige_tijd))
    verlopen_herinneringen = c_actief.fetchall()

    for herinnering in verlopen_herinneringen:
        c_verlopen.execute("INSERT INTO verlopen_herinneringen (beschrijving, datum, tijd) VALUES (?, ?, ?)",
                           (herinnering[0], herinnering[1], herinnering[2]))
        c_actief.execute("DELETE FROM herinneringen WHERE beschrijving = ? AND datum = ? AND tijd = ?",
                         (herinnering[0], herinnering[1], herinnering[2]))

    conn_verlopen.commit()
    conn_actief.commit()
    conn_actief.close()
    conn_verlopen.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        stop_flag = True
        lcd_clear()
        GPIO.cleanup()
        print("Programma gestopt.")