import time
import numpy as np
import serial
from mss import mss

PORT = "COM12"
BAUD = 115200
FPS = 20
STEP = 20

def avg_rgb(bgr):
    small = bgr[::STEP, ::STEP, :3]
    b,g,r = small.mean(axis=(0,1))
    return int(r), int(g), int(b)

def main():
    ser = serial.Serial(PORT, BAUD, timeout=1, write_timeout=1, dsrdtr=False, rtscts=False)
    ser.setDTR(False); time.sleep(1)
    ser.reset_input_buffer(); ser.reset_output_buffer()
    ser.setDTR(True); time.sleep(2)

    sct = mss()
    mon = sct.monitors[2]
    x,y,w,h = mon["left"], mon["top"], mon["width"], mon["height"]

    zones = {
        "LEFT":  {"left": x,      "top": y,      "width": w//4, "height": h},
        "TOP":   {"left": x,      "top": y,      "width": w,    "height": h//4},
        "RIGHT": {"left": x+w*3//4,"top": y,      "width": w//4, "height": h},
    }

    delay = 1.0 / FPS

    while True:
        t0 = time.time()

        l = np.array(sct.grab(zones["LEFT"]))
        t = np.array(sct.grab(zones["TOP"]))
        r = np.array(sct.grab(zones["RIGHT"]))

        r1,g1,b1 = avg_rgb(l)
        r2,g2,b2 = avg_rgb(t)
        r3,g3,b3 = avg_rgb(r)

        line = f"{r1},{g1},{b1}|{r2},{g2},{b2}|{r3},{g3},{b3}\n"
        ser.write(line.encode("ascii"))

        dt = time.time() - t0
        if dt < delay:
            time.sleep(delay - dt)

if __name__ == "__main__":
    main()
