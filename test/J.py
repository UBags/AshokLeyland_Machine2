from BaseUtils import *
from utils.CosThetaFileUtils import *
import winsound
# os.makedirs(os.path.dirname(get_project_root() + '/' + 'ABCD/EFGH/abcd.txt'), exist_ok=True)
# createDirectory("ABCD.EFGH/IJKL")
# winsound.PlaySound('SystemExit', winsound.SND_ALIAS)
# winsound.PlaySound('SystemExclamation', winsound.SND_ALIAS)
# winsound.PlaySound("SystemExit", winsound.SND_ALIAS)
# winsound.Beep(1500,400)
# print("\a") - for Mac
notes = {'C': 1635,
         'D': 1835,
         'E': 2060,
         'S': 1945,
         'F': 2183,
         'G': 2450,
         'A': 2750,
         'B': 3087,
         ' ': 37}
melodie = 'CDEFG G AAAAG AAAAG FFFFE E DDDDC'
# for note in melodie:
#     winsound.Beep(notes[note], 500)

winsound.PlaySound("C:\PythonProjects\Geltec_New\wavs\System disconnected.wav", winsound.SND_FILENAME)

winsound.PlaySound("C:\PythonProjects\Geltec_New\wavs\System is ready.wav", winsound.SND_FILENAME)

winsound.PlaySound("C:\PythonProjects\Geltec_New\wavs\Siren.wav", winsound.SND_FILENAME)

import math
import wave
import struct

def synthComplex(freq=[440],coef=[1], datasize=10000, fname="test.wav"):
    frate = 44100.00
    amp=8000.0
    sine_list=[]
    for x in range(datasize):
        samp = 0
        for k in range(len(freq)):
            samp = samp + coef[k] * math.sin(2*math.pi*freq[k]*(x/frate))
        sine_list.append(samp)
    wav_file=wave.open(fname,"w")
    nchannels = 1
    sampwidth = 2
    framerate = int(frate)
    nframes=datasize
    comptype= "NONE"
    compname= "not compressed"
    wav_file.setparams((nchannels, sampwidth, framerate, nframes, comptype, compname))
    for s in sine_list:
        wav_file.writeframes(struct.pack('h', int(s*amp/2)))
    wav_file.close()

synthComplex([440,880,1200], [0.4,0.3,0.1], 30000, "tone.wav")