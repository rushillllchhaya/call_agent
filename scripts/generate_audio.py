import wave
import math
import struct
import os

def generate_beep(filename="app/static/welcome.wav", duration=5.0, freq=440.0):
    # Ensure directory exists
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    
    sample_rate = 44100
    n_samples = int(sample_rate * duration)
    amplitude = 16000
    
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        
        for i in range(n_samples):
            # Generate a sine wave (beep)
            value = int(amplitude * math.sin(2 * math.pi * freq * i / sample_rate))
            data = struct.pack('<h', value)
            wav_file.writeframesraw(data)
            
    print(f"Generated {filename}")

if __name__ == "__main__":
    generate_beep()
