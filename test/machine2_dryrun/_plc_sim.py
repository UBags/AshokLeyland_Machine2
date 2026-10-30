"""Simulated PLC for the IOServer dry runs, for either IOServer.

    from _plc_sim import setup
    IOServer, TAGS, WRITES, LOCK, VENDOR = setup()

The IOServer is chosen by the environment variable COSTHETA_PLC_VENDOR (as in Configuration.getPlcVendor()):
  * allenbradley (default): costhetaio.allenbradley.IOServer with pycomm3's LogixDriver replaced by an in-memory
    table of tag values;
  * siemens: costhetaio.siemens.IOServer with pyS7's S7Client replaced by an in-memory S7 data block. Every read and
    write goes through the real PyS7PLCBackend: tag name -> DB500 address (IOServerProperties.yaml) -> bytes encoded
    as the S7 CPU stores them (bits in bytes, INT big-endian 16-bit, REAL big-endian IEEE 754). Two tags sharing an
    address would therefore disturb each other here exactly as on the real PLC. Bit reads fail the first time with
    INVALID_DATA_SIZE, so the byte-read fall-back taken from s7_smoke_test.py is exercised too.

TAGS[tag] reads / writes a tag as the PLC sees it (the test plays the PLC); WRITES collects every (tag, value) the PC
writes; LOCK is the lock the tests hold while changing several tags together.
"""
import collections, os, struct, threading

WRITES = []
LOCK = threading.Lock()


def setup():
    vendor = (os.environ.get("COSTHETA_PLC_VENDOR") or "allenbradley").strip().lower()
    if vendor == "siemens":
        return _setup_siemens()
    return _setup_allenbradley()


# ----------------------------------------------------------------------------------------------- Allen-Bradley
class _V:
    def __init__(self, v): self.value = v


def _setup_allenbradley():
    TAGS = collections.defaultdict(lambda: False)

    class FakeLogix:
        def __init__(self, ip): self.connected = False
        def open(self): self.connected = True
        def close(self): self.connected = False
        def read(self, *tags):
            with LOCK:
                return _V(TAGS[tags[0]])
        def write(self, tag, value):
            with LOCK:
                TAGS[tag] = value; WRITES.append((tag, value))

    import costhetaio.allenbradley.IOServer as IOS
    IOS.LogixDriver = FakeLogix
    return IOS.IOServer, TAGS, WRITES, LOCK, "allenbradley"


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


def _setup_siemens():
    os.environ["COSTHETA_PLC_VENDOR"] = "siemens"
    import costhetaio.siemens.plc_backend as PB
    _, tagmap = PB.load_tag_map()
    by_address = {spec[0]: name for name, spec in tagmap.items()}
    MEM = S7Memory()
    failed_bit_once = set()

    class FakeS7Client:
        """Stand-in for pyS7.S7Client (same surface as used by PyS7PLCBackend / s7_smoke_test.py)."""
        def __init__(self, address=None, rack=0, slot=1, **kwargs):
            self.address = address; self._connected = False
        def connect(self): self._connected = True
        def disconnect(self): self._connected = False
        @property
        def is_connected(self): return self._connected
        def read(self, addresses):
            out = []
            for a in addresses:
                if ",X" in a and a not in failed_bit_once:
                    failed_bit_once.add(a)
                    raise Exception("S7ReadResponseError: INVALID_DATA_SIZE")      # exercise the byte fall-back
                out.append(MEM.get(a))
            return out
        def write(self, addresses, values):
            for a, v in zip(addresses, values):
                MEM.set(a, v)
                WRITES.append((by_address.get(a, a), MEM.get(a)))

    PB.S7Client = FakeS7Client

    class TagView:
        """TAGS[tag]: the tag's value in PLC memory, by name (reading / writing as the PLC)."""
        def __getitem__(self, tag): return MEM.get(tagmap[tag][0])
        def __setitem__(self, tag, value): MEM.set(tagmap[tag][0], value)

    import costhetaio.siemens.IOServer as S
    return S.IOServer, TagView(), WRITES, LOCK, "siemens"
