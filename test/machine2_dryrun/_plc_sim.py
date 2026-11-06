"""Simulated PLC for the IOServer dry runs, for either IOServer.

    from _plc_sim import setup
    IOServer, TAGS, WRITES, LOCK, VENDOR = setup()

The IOServer is costhetaio.commonplc.PLCIOServer; the PLC make is Configuration.getPlcVendor(): the environment
variable COSTHETA_PLC_VENDOR if set, else plc.vendor in the properties file (siemens in the repo's file). Only the
driver under that vendor's PLCDataIO is simulated:
  * allenbradley: AllenBradleyPLCDataIO with pycomm3's LogixDriver replaced by an in-memory table of tag
    values;
  * siemens: SiemensPLCDataIO with pyS7's S7Client replaced by an in-memory S7 data block. Every read and
    write goes through the real SiemensPLCDataIO and PyS7PLCBackend: tag name -> DB500 address (IOServerProperties.yaml) -> bytes encoded
    as the S7 CPU stores them (bits in bytes, INT big-endian 16-bit, REAL / LREAL big-endian IEEE 754 32 / 64-bit). Two tags sharing an
    address would therefore disturb each other here exactly as on the real PLC. Bit reads fail the first time with
    INVALID_DATA_SIZE, so the byte-read fall-back taken from costhetaio/siemens/test/s7_smoke_test.py is exercised too.

TAGS[tag] reads / writes a tag as the PLC sees it (the test plays the PLC); WRITES collects every (tag, value) the PC
writes; LOCK is the lock the tests hold while changing several tags together.

State-2 cycle settings: setting_tags(IOServer) gives the settings tags configured for the vendor (empty names = not on
this PLC). Both PLCs take the settings from machine_settings in the database (cycle.settings.source=database), so
setup(mode) creates that database and puts the default settings (30 rpm, 15 / 19 Nm, 1 / 1 rotations) in force;
use_machine_settings(mode, ...) puts other values in force (as the frontend's Machine Settings dialog does).
"""
import collections, os, struct, threading

WRITES = []
LOCK = threading.Lock()

# Connection and restart simulation (both vendors):
#   LINK["down"] = True   the network link to the PLC is cut: open / connect fail, every read and write raises,
#                         the clients report not connected; the PLC itself keeps running and keeps its memory.
#   plc_restart()         the PLC restarts: all its memory goes back to the start values (False / 0), except the
#                         watch tag, which its program sets to 1234 again.
#   STUCK                 tag names whose writes the PLC ignores (to test write verification).
#   ERRORS                tag names the driver fails on, each in its own way: pycomm3 returns a result with .error
#                         set (no exception), pyS7 raises. The PLCDataIO must turn both into PLCIOError.
LINK = {"down": False}
STUCK = set()
ERRORS = set()
_RESTART = []           # set by the vendor setup: the function that clears the PLC memory


def plc_restart(watch_tag=None, watch_value=1234):
    _RESTART[0](watch_tag, watch_value)


# COSTHETA_WRITE_TRACE=<file>: every write the PC makes is also appended to that file as "tag<TAB>value", in order
# (to compare two versions of the IOServer: the same scenarios must make the same writes).
_TRACE_FILE = os.environ.get("COSTHETA_WRITE_TRACE")


def _trace(tag, value):
    if _TRACE_FILE:
        with open(_TRACE_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"{tag}\t{value!r}\n")


SETTING_ATTRS = ("PC_PLC_LH_RH_Selection", "PC_PLC_ModelSelection",
                 "PC_PLC_TighteningTorque1Setpoint", "PC_PLC_TighteningTorque2Setpoint",
                 "PC_PLC_RotationRPM1", "PC_PLC_RotationRPM2",
                 "PC_PLC_RotationRPM", "PC_PLC_NoOfRotation1CCW", "PC_PLC_NoOfRotation1CW")


def setting_tags(IOServer):
    """Tag names of the state-2 settings this PLC has (configured, non-empty)."""
    return tuple(getattr(IOServer, a) for a in SETTING_ATTRS if getattr(IOServer, a, ""))


def use_machine_settings(mode="Test", **values):
    """Create the mode's database if needed and put these settings in force (defaults for any not given)."""
    from persistence import Persistence as P
    P.setDatabaseName(mode, createDB=True)
    settings = dict(P.DEFAULT_MACHINE_SETTINGS); settings.update(values)
    assert P.insertNewMachineSettings(P.getDatabaseName(), updated_by="dryrun", **settings), settings
    return settings


# Simulated heartbeat server (the IOServer acts on each server's status, from the heartbeat server, and takes the
# heartbeat server itself as down after ioserver.heartbeat.server.silence.sec without a message). The dry runs have no
# heartbeat server, so setup() starts this one: every 1 s it sends the statuses in HEARTBEAT (all ALIVE to start).
#   HEARTBEAT["db"] = False         the database server is reported down (likewise "camera", "fe", "qrcode", "io")
#   HEARTBEAT["silent"] = True      no messages: the heartbeat server is down
# COSTHETA_DRYRUN_NO_HEARTBEAT_SIM=1: not started (a real heartbeat server is running).
HEARTBEAT = {"camera": True, "qrcode": True, "io": True, "db": True, "fe": True, "redis": True, "silent": False}
_HB_THREAD = []


def start_heartbeat_sim():
    if _HB_THREAD or os.environ.get("COSTHETA_DRYRUN_NO_HEARTBEAT_SIM"):
        return
    import time as _time
    from redis import Redis
    from utils.RedisUtils import sendCombinedHeartbeatFromHeartbeatServerToIOServer, ALIVE, DEAD

    def run():
        r = Redis("localhost", 6379)
        while True:
            try:
                if not HEARTBEAT["silent"]:
                    statuses = {k: (ALIVE if HEARTBEAT[k] else DEAD) for k in ("camera", "qrcode", "io", "db", "fe", "redis")}
                    allok = all(HEARTBEAT[k] for k in ("camera", "qrcode", "io", "db", "fe"))
                    sendCombinedHeartbeatFromHeartbeatServerToIOServer(r, ALIVE if allok else DEAD,
                                                                        serverStatuses=statuses)
            except Exception:
                pass
            _time.sleep(1.0)

    t = threading.Thread(target=run, name="simulated heartbeat server", daemon=True)
    t.start()
    _HB_THREAD.append(t)


def setup(mode="Test"):
    start_heartbeat_sim()
    # The PLC make the IOServer will use: COSTHETA_PLC_VENDOR if set, else plc.vendor in the properties file - exactly as
    # PLCIOServer / PLCDataIOFactory decide. It is then pinned in the environment, so the driver simulated here and the
    # PLCDataIO the server creates are always the same make (otherwise the server would reach for the real PLC).
    from Configuration import CosThetaConfigurator
    vendor = CosThetaConfigurator.getPlcVendor()
    os.environ["COSTHETA_PLC_VENDOR"] = vendor
    if CosThetaConfigurator.getInstance().getCycleSettingsSource() == "database":
        use_machine_settings(mode)
    if vendor == "siemens":
        return _setup_siemens()
    return _setup_allenbradley()


# ----------------------------------------------------------------------------------------------- Allen-Bradley
class _V:
    def __init__(self, v, error=None): self.value, self.error = v, error


def _setup_allenbradley():
    TAGS = collections.defaultdict(lambda: False)

    class FakeLogix:
        def __init__(self, ip): self._open = False
        @property
        def connected(self): return self._open and not LINK["down"]
        def open(self):
            if LINK["down"]: raise Exception("simulated: PLC not reachable")
            self._open = True
        def close(self): self._open = False
        def read(self, *tags):
            if LINK["down"]: raise Exception("simulated: link down")
            if tags[0] in ERRORS: return _V(None, "simulated: tag read refused")       # pycomm3: error in the result
            with LOCK:
                return _V(TAGS[tags[0]])
        def write(self, tag, value):
            if LINK["down"]: raise Exception("simulated: link down")
            if tag in ERRORS: return _V(value, "simulated: tag write refused")         # pycomm3: error in the result
            with LOCK:
                if tag not in STUCK:
                    TAGS[tag] = value
                WRITES.append((tag, value))
                _trace(tag, value)

    def restart(watch_tag, watch_value):
        with LOCK:
            TAGS.clear()
            if watch_tag: TAGS[watch_tag] = watch_value
    _RESTART[:] = [restart]

    import costhetaio.allenbradley.AllenBradleyPLCDataIO as ABD
    ABD.LogixDriver = FakeLogix
    from costhetaio.commonplc.PLCIOServer import PLCIOServer
    return PLCIOServer, TAGS, WRITES, LOCK, "allenbradley"


# ----------------------------------------------------------------------------------------------- Siemens
class S7Memory:
    """The PLC's data blocks, byte for byte."""

    def __init__(self):
        self.dbs = collections.defaultdict(lambda: bytearray(512))
        self.lock = threading.RLock()

    def get(self, address):
        from costhetaio.siemens.plc_backend import parse_address
        db, letter, byte, bit = parse_address(address)
        with self.lock:
            m = self.dbs[db]
            if letter == "X": return bool((m[byte] >> bit) & 1)
            if letter == "B": return m[byte]
            if letter == "I": return struct.unpack(">h", m[byte:byte + 2])[0]
            if letter == "DI": return struct.unpack(">i", m[byte:byte + 4])[0]
            if letter == "R": return struct.unpack(">f", m[byte:byte + 4])[0]
            if letter == "LR": return struct.unpack(">d", m[byte:byte + 8])[0]

    def set(self, address, value):
        from costhetaio.siemens.plc_backend import parse_address
        db, letter, byte, bit = parse_address(address)
        with self.lock:
            m = self.dbs[db]
            if letter == "X":
                m[byte] = (m[byte] | (1 << bit)) if value else (m[byte] & ~(1 << bit) & 0xFF)
            elif letter == "B": m[byte] = int(value) & 0xFF
            elif letter == "I": m[byte:byte + 2] = struct.pack(">h", int(value))
            elif letter == "DI": m[byte:byte + 4] = struct.pack(">i", int(value))
            elif letter == "R": m[byte:byte + 4] = struct.pack(">f", float(value))
            elif letter == "LR": m[byte:byte + 8] = struct.pack(">d", float(value))


def _setup_siemens():
    os.environ["COSTHETA_PLC_VENDOR"] = "siemens"
    import costhetaio.siemens.plc_backend as PB
    _, tagmap = PB.load_tag_map()
    by_address = {spec[0]: name for name, spec in tagmap.items()}
    MEM = S7Memory()
    failed_bit_once = set()

    class FakeS7Client:
        """Stand-in for pyS7.S7Client (same surface as used by PyS7PLCBackend / test/s7_smoke_test.py)."""
        def __init__(self, address=None, rack=0, slot=1, **kwargs):
            self.address = address; self._connected = False
        def connect(self):
            if LINK["down"]: raise Exception("simulated: PLC not reachable")
            self._connected = True
        def disconnect(self): self._connected = False
        @property
        def is_connected(self): return self._connected and not LINK["down"]
        def read(self, addresses):
            if LINK["down"]: raise Exception("simulated: link down")
            out = []
            if any(by_address.get(a) in ERRORS for a in addresses):
                raise Exception("simulated: S7ReadResponseError OBJECT_DOES_NOT_EXIST")     # pyS7: raises
            for a in addresses:
                if ",X" in a and a not in failed_bit_once:
                    failed_bit_once.add(a)
                    raise Exception("S7ReadResponseError: INVALID_DATA_SIZE")      # exercise the byte fall-back
                out.append(MEM.get(a))
            return out
        def write(self, addresses, values):
            if LINK["down"]: raise Exception("simulated: link down")
            if any(by_address.get(a) in ERRORS for a in addresses):
                raise Exception("simulated: S7WriteResponseError OBJECT_DOES_NOT_EXIST")    # pyS7: raises
            for a, v in zip(addresses, values):
                if by_address.get(a, a) not in STUCK:
                    MEM.set(a, v)
                WRITES.append((by_address.get(a, a), v if by_address.get(a, a) in STUCK else MEM.get(a)))
                _trace(*WRITES[-1])

    def restart(watch_tag, watch_value):
        with MEM.lock:
            MEM.dbs.clear()
        if watch_tag: MEM.set(tagmap[watch_tag][0], watch_value)
    _RESTART[:] = [restart]

    PB.S7Client = FakeS7Client

    class TagView:
        """TAGS[tag]: the tag's value in PLC memory, by name (reading / writing as the PLC)."""
        def __getitem__(self, tag): return MEM.get(tagmap[tag][0])
        def __setitem__(self, tag, value): MEM.set(tagmap[tag][0], value)
        def raw(self, tag):
            """The tag's bytes in PLC memory exactly as the CPU holds them (size from the address type)."""
            db, letter, byte, bit = PB.parse_address(tagmap[tag][0])
            with MEM.lock: return bytes(MEM.dbs[db][byte:byte + max(1, PB._SIZES[letter])])

    from costhetaio.commonplc.PLCIOServer import PLCIOServer
    return PLCIOServer, TagView(), WRITES, LOCK, "siemens"
