import hashlib
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lavati import engine, mediatags, regcmp, versioninfo  # noqa: E402


# ------------------------------------------------------------------ builders
def synchsafe(n):
    return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])


def id3_frame(fid, payload):
    return fid.encode() + struct.pack(">I", len(payload)) + b"\x00\x00" + payload


def mp3_bytes(title, artist="Band", audio_seed=b"A", frames=10, with_v1=False, year="1999"):
    tag = (id3_frame("TIT2", b"\x00" + title.encode("latin-1")) + id3_frame("TPE1", b"\x03" + artist.encode()) +
           id3_frame("TYER", b"\x00" + year.encode()) + id3_frame("TCON", b"\x00(17)") +
           id3_frame("COMM", b"\x00eng\x00" + b"nice song") +
           id3_frame("APIC", b"\x00image/png\x00\x03\x00" + b"\x89PNGfake"))
    head = b"ID3\x03\x00\x00" + synchsafe(len(tag)) + tag
    frame = b"\xff\xfb\x90\x00" + audio_seed * (417 - 4)
    data = head + frame * frames
    if with_v1:
        data += b"TAG" + title.encode().ljust(30, b"\0") + artist.encode().ljust(30, b"\0") + b"\0" * 30 + b"2001" + b"\0" * 30 + bytes([17])
    return data


def flac_bytes(title, audio=b"\x00" * 50, md5=b"\x11" * 16, artist="Band"):
    si_val = (44100 << 44) | (1 << 41) | (15 << 36) | (44100 * 3)
    si = struct.pack(">HHBBBBBB", 4096, 4096, 0, 0, 0, 0, 0, 0)[:10] + si_val.to_bytes(8, "big") + md5
    vendor = b"libFLAC test"
    comments = [f"TITLE={title}".encode(), f"ARTIST={artist}".encode(), b"date=2001"]
    vc = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
    for c in comments:
        vc += struct.pack("<I", len(c)) + c
    return (b"fLaC" + bytes([0]) + len(si).to_bytes(3, "big") + si + bytes([0x84]) + len(vc).to_bytes(3, "big") + vc + audio)


def atom(typ, payload):
    return struct.pack(">I", 8 + len(payload)) + typ.encode("latin-1") + payload


def mp4_bytes(title, mdat=b"AUDIO" * 20, artist="Band"):
    item = lambda k, v: atom(k, atom("data", struct.pack(">II", 1, 0) + v.encode()))
    ilst = atom("ilst", item("\xa9nam", title) + item("\xa9ART", artist) +
                atom("trkn", atom("data", struct.pack(">II", 0, 0) + struct.pack(">HHHH", 0, 3, 12, 0))))
    meta = atom("meta", b"\x00\x00\x00\x00" + ilst)
    mvhd = atom("mvhd", b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", 1000, 180000) + b"\x00" * 80)
    moov = atom("moov", mvhd + atom("udta", meta))
    return atom("ftyp", b"M4A \x00\x00\x00\x00M4A mp42") + moov + atom("mdat", mdat)


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_mp3(self):
        p = self.write("a.mp3", mp3_bytes("Hello", with_v1=True))
        info = mediatags.read_media(p)
        tags = dict(info["tags"])
        self.assertEqual(tags["Title"], "Hello")
        self.assertEqual(tags["Artist"], "Band")
        self.assertEqual(tags["Genre"], "Rock")
        self.assertEqual(tags["Year"], "1999")
        self.assertEqual(tags["Comment"], "nice song")
        self.assertEqual(tags["ID3v1 Title"], "Hello")
        self.assertEqual(len(info["pics"]), 1)
        props = dict(info["props"])
        self.assertIn("Layer 3", props["Format"])
        self.assertEqual(props["Sample rate"], "44100 Hz")
        self.assertIn("128 kbps", props["Bitrate"])
        # audio fingerprint ignores tags
        p2 = self.write("b.mp3", mp3_bytes("A completely different title", artist="Other", with_v1=False))
        self.assertEqual(mediatags.read_media(p2)["audio"], info["audio"])
        p3 = self.write("c.mp3", mp3_bytes("Hello", audio_seed=b"Z"))
        self.assertNotEqual(mediatags.read_media(p3)["audio"], info["audio"])

    def test_flac(self):
        p = self.write("a.flac", flac_bytes("Song"))
        info = mediatags.read_media(p)
        tags = dict(info["tags"])
        self.assertEqual(tags["Title"], "Song")
        self.assertEqual(tags["Year"], "2001")
        props = dict(info["props"])
        self.assertEqual(props["Sample rate"], "44100 Hz")
        self.assertEqual(props["Channels"], "2")
        self.assertEqual(props["Bits per sample"], "16")
        self.assertEqual(props["Duration"], "0:03.000")
        p2 = self.write("b.flac", flac_bytes("Other title"))
        self.assertEqual(mediatags.read_media(p2)["audio"], info["audio"])

    def test_mp4(self):
        p = self.write("a.m4a", mp4_bytes("Tune"))
        info = mediatags.read_media(p)
        tags = dict(info["tags"])
        self.assertEqual(tags["Title"], "Tune")
        self.assertEqual(tags["Track"], "3/12")
        self.assertEqual(dict(info["props"])["Duration"], "3:00.000")
        p2 = self.write("b.m4a", mp4_bytes("Different"))
        self.assertEqual(mediatags.read_media(p2)["audio"], info["audio"])
        p3 = self.write("c.m4a", mp4_bytes("Tune", mdat=b"OTHER"))
        self.assertNotEqual(mediatags.read_media(p3)["audio"], info["audio"])

    def test_rows(self):
        a = mediatags.rows_for(self.write("a.mp3", mp3_bytes("One")))
        b = mediatags.rows_for(self.write("b.flac", flac_bytes("One")))
        ta = {k: v for g, k, v in a if g == "Tags"}
        tb = {k: v for g, k, v in b if g == "Tags"}
        self.assertEqual(ta["Title"], tb["Title"])         # tag names are normalised across formats
        self.assertTrue(any(g == "Audio data" for g, k, v in a))

    def test_errors(self):
        p = self.write("bad.m4a", b"nothing here at all")
        with self.assertRaises(mediatags.MediaError):
            mediatags.read_media(p)


class VersionTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "needs Windows system binaries")
    def test_system_binary(self):
        rows = versioninfo.read_info(sys.executable)
        d = {(g, k): v for g, k, v in rows}
        self.assertEqual(d[("File", "Type")], "Executable")
        self.assertIn("x64", d[("File", "Machine")]) if "64" in sys.version.split("[")[-1] else None
        self.assertTrue(d[("Strings", "ProductName")])
        self.assertRegex(d[("Fixed version info", "File version")], r"^\d+\.\d+\.\d+\.\d+$")
        rows = versioninfo.read_info(os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "kernel32.dll"))
        self.assertEqual({(g, k): v for g, k, v in rows}[("File", "Type")], "DLL")

    def test_not_a_pe(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.exe")
        with open(p, "wb") as f:
            f.write(b"hello world")
        with self.assertRaises(versioninfo.PEError):
            versioninfo.read_info(p)
        shutil.rmtree(d, ignore_errors=True)

    def test_synthetic_version_block(self):
        def blk(key, value=b"", vlen=0, typ=0, children=b""):
            kb = key.encode("utf-16-le") + b"\0\0"
            body = struct.pack("<HHH", 0, vlen, typ) + kb
            body += b"\0" * ((4 - len(body) % 4) % 4)
            body += value
            body += b"\0" * ((4 - len(body) % 4) % 4)
            body += children
            return struct.pack("<H", len(body)) + body[2:]
        s1 = blk("ProductName", "Widget\0".encode("utf-16-le"), 7, 1)
        s2 = blk("FileVersion", "1.2.3.4\0".encode("utf-16-le"), 8, 1)
        table = blk("040904B0", children=s1 + (b"\0" * ((4 - len(s1) % 4) % 4)) + s2)
        sfi = blk("StringFileInfo", children=table)
        fixed = struct.pack("<13I", 0xFEEF04BD, 0x10000, (1 << 16) | 2, (3 << 16) | 4, (1 << 16) | 2, (3 << 16) | 4, 0x3F, 0, 0x40004, 1, 0, 0, 0)
        root = blk("VS_VERSION_INFO", fixed, 52, 0, sfi)
        v = versioninfo.parse_version_resource(root)
        self.assertEqual(v["fixed"]["file_version"], "1.2.3.4")
        self.assertEqual(v["strings"]["040904B0"]["ProductName"], "Widget")
        self.assertEqual(v["strings"]["040904B0"]["FileVersion"], "1.2.3.4")


REG_A = '''Windows Registry Editor Version 5.00

[HKEY_CURRENT_USER\\Software\\Acme]
@="default"
"Name"="Acme Tool"
"Count"=dword:0000000a
"Path"=hex(2):25,00,50,00,41,00,54,00,48,00,25,00,00,00
"Blob"=hex:01,02,03,\\
  04,05

[HKEY_CURRENT_USER\\Software\\Acme\\Sub]
"Only"="left"
"Esc"="a\\\\b \\"q\\""
'''
REG_B = '''Windows Registry Editor Version 5.00

[HKEY_CURRENT_USER\\Software\\Acme]
@="default"
"Name"="Acme Tool 2"
"count"=dword:0000000a
"Blob"=hex:01,02,03,04,05
"New"="right"

[HKEY_CURRENT_USER\\Software\\Acme\\Other]
"X"=dword:00000001
'''


class RegistryTests(unittest.TestCase):
    def test_parse(self):
        r = regcmp.parse_reg(REG_A)
        acme = r["HKEY_CURRENT_USER\\Software\\Acme"]
        self.assertEqual(acme["@"], ("REG_SZ", "default"))
        self.assertEqual(acme["Count"], ("REG_DWORD", "0x0000000a (10)"))
        self.assertEqual(acme["Path"], ("REG_EXPAND_SZ", "%PATH%"))
        self.assertEqual(acme["Blob"], ("REG_BINARY", "01 02 03 04 05"))
        self.assertEqual(r["HKEY_CURRENT_USER\\Software\\Acme\\Sub"]["Esc"], ("REG_SZ", 'a\\b "q"'))
        self.assertEqual(regcmp.normalize_root("HKCU\\Software"), "HKEY_CURRENT_USER\\Software")
        self.assertTrue(regcmp.is_registry_spec("HKLM\\SOFTWARE\\X"))
        self.assertTrue(regcmp.is_registry_spec("registry:HKEY_CURRENT_USER"))
        self.assertFalse(regcmp.is_registry_spec(r"C:\\dir\\file.reg"))

    def test_file_encodings(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "u.reg")
        with open(p, "wb") as f:
            f.write(REG_A.encode("utf-16"))
        self.assertIn("HKEY_CURRENT_USER\\Software\\Acme", regcmp.load_reg_file(p))
        shutil.rmtree(d, ignore_errors=True)

    def test_compare(self):
        root = regcmp.compare_registry(regcmp.parse_reg(REG_A), regcmp.parse_reg(REG_B))[0]
        by = {n.rel: n for n in engine.walk([root])}
        self.assertEqual(by["name"].status, "changed")
        self.assertEqual(by["count"].status, "same")            # value names are case-insensitive
        self.assertEqual(by["blob"].status, "same")
        self.assertEqual(by["new"].status, "right")
        self.assertEqual(by["sub"].status, "left")
        self.assertEqual(by["other"].status, "right")
        self.assertEqual(by["sub\\only"].status, "left")
        self.assertEqual(root.status, "changed")
        counts = regcmp.count_values([root])
        self.assertEqual((counts["changed"], counts["left"], counts["right"]), (1, 3, 2))

    @unittest.skipUnless(sys.platform == "win32", "live registry is Windows only")
    def test_live(self):
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\LavatiCompareTest")
        winreg.SetValueEx(key, "A", 0, winreg.REG_SZ, "one")
        winreg.SetValueEx(key, "N", 0, winreg.REG_DWORD, 7)
        winreg.SetValueEx(key, "M", 0, winreg.REG_MULTI_SZ, ["x", "y"])
        sub = winreg.CreateKey(key, "Child")
        winreg.SetValueEx(sub, "B", 0, winreg.REG_BINARY, b"\x01\x02")
        try:
            r = regcmp.load("HKCU\\Software\\LavatiCompareTest")
            top = r["HKEY_CURRENT_USER\\Software\\LavatiCompareTest"]
            self.assertEqual(top["A"], ("REG_SZ", "one"))
            self.assertEqual(top["N"], ("REG_DWORD", "0x00000007 (7)"))
            self.assertEqual(top["M"], ("REG_MULTI_SZ", "x | y"))
            self.assertEqual(r["HKEY_CURRENT_USER\\Software\\LavatiCompareTest\\Child"]["B"], ("REG_BINARY", "01 02"))
            # compare live branch against an export of itself
            n = regcmp.compare_registry(r, r)[0]
            self.assertEqual(n.status, "same")
        finally:
            winreg.DeleteKey(sub, "") if False else None
            winreg.CloseKey(sub)
            winreg.CloseKey(key)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\LavatiCompareTest\Child")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\LavatiCompareTest")


if __name__ == "__main__":
    unittest.main()
