"""FE -> Redis(fe2db.q) -> DBServer -> PostgreSQL, using the real DBServer process code."""
import sys, os, time, threading, numpy as np, cv2, psycopg2
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from redis import Redis
from utils.RedisUtils import sendDataFromFEServerToDatabaseServer, ok, notok, clearQueues
import persistence.Persistence as P
from persistence.DBServer import startDBServer

r = Redis('localhost', 6379); clearQueues(r)
threading.Thread(target=startDBServer, kwargs=dict(mode="Test", username="admin"), daemon=True).start()
time.sleep(3)

def img(txt):
    a = np.full((720, 1280, 3), 90, np.uint8); cv2.putText(a, txt, (300, 360), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 5); return a
t = "2026-10-30 18:00:00"
qr = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
sent = sendDataFromFEServerToDatabaseServer(r, qrCode=qr,
    knucklePicture=img("knuckle"), knuckleResult=ok, knuckleDatetime=t,
    hubAndBottomBearingPicture=img("hub"), hubAndBottomBearingResult=ok, hubAndBottomBearingDatetime=t,
    topBearingPicture=img("top"), topBearingResult=ok, topBearingDatetime=t,
    nutAndPlateWasherPicture=img("nut"), nutAndPlateWasherResult=ok, nutAndPlateWasherDatetime=t,
    tighteningTorque1=19.08, tighteningTorque1Result=ok, tighteningTorque1Datetime=t,
    componentPressBunkCheckingPicture=img("bunk1"), componentPressBunkCheckingResult=ok, componentPressBunkCheckDatetime=t,
    componentPressDone=ok, componentPressDoneDatetime=t,
    noBunkCheckingPicture=img("nobunk"), noBunkCheckingResult=ok, noBunkCheckDatetime=t,
    tighteningTorque2=19.03, tighteningTorque2Result=ok, tighteningTorque2Datetime=t,
    splitPinAndWasherPicture=img("splitpin"), splitPinAndWasherResult=ok, splitPinAndWasherDatetime=t,
    capCheckingPicture=img("cap"), capCheckingResult=ok, capCheckingDatetime=t,
    bunkCheckingPicture=img("bunk2"), capPressBunkCheckingResult=ok, capPressBunkCheckDatetime=t,
    pressDone=ok, capPressDoneDatetime=t,
    freeRotationTorque1=2.41, freeRotationTorque1Result=ok, freeRotationTorque1Datetime=t,
    overallResult=ok)
print("FE sent:", sent)
time.sleep(12)
c = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", database="ashok_leyland_test"); cur = c.cursor()
cur.execute("""select qr_code, model_name, lhs_rhs, nut_tightening_torque_1, nut_tightening_torque_2, free_rotation_torque_1,
               component_press_done, cap_press_done, ok_notok_result, remarks, check_knuckle_imagefile
               from al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data where qr_code like '%11770%' order by record_id desc limit 1""")
row = cur.fetchone(); print("DB row:", row); c.close()
print("knuckle image file exists:", os.path.exists(row[-1]) if row else None)
# report paths used by the frontend menus
for fn in ["getAllDataOfToday"]:
    f = getattr(P, fn, None)
    if f:
        try: res = f(db_name="ashok_leyland_test"); print(fn, "->", type(res).__name__, (len(res) if hasattr(res, '__len__') else res))
        except Exception as e: print(fn, "FAILED", e)
os._exit(0)
