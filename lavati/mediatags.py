"""Tags and audio-data fingerprints for MP3, FLAC and MP4/M4A/AAC files. Pure Python, no Qt."""
import hashlib
import os
import struct

ID3_FRAMES = {
    "TIT2": "Title", "TPE1": "Artist", "TALB": "Album", "TPE2": "Album artist", "TRCK": "Track", "TPOS": "Disc",
    "TYER": "Year", "TDRC": "Year", "TCON": "Genre", "TCOM": "Composer", "TBPM": "BPM", "TPUB": "Publisher",
    "TENC": "Encoded by", "TSSE": "Encoder settings", "TCOP": "Copyright", "TLAN": "Language", "TIT3": "Subtitle",
    "TOPE": "Original artist", "TKEY": "Key", "TLEN": "Length (ms)", "TSRC": "ISRC", "COMM": "Comment",
    "USLT": "Lyrics", "TDAT": "Date", "TIME": "Time",
    # ID3v2.2 three-letter ids
    "TT2": "Title", "TP1": "Artist", "TAL": "Album", "TP2": "Album artist", "TRK": "Track", "TPA": "Disc",
    "TYE": "Year", "TCO": "Genre", "TCM": "Composer", "TBP": "BPM", "TPB": "Publisher", "TEN": "Encoded by",
    "TSS": "Encoder settings", "TCR": "Copyright", "COM": "Comment", "ULT": "Lyrics",
}
ID3V1_GENRES = ("Blues,Classic Rock,Country,Dance,Disco,Funk,Grunge,Hip-Hop,Jazz,Metal,New Age,Oldies,Other,Pop,R&B,Rap,Reggae,"
                "Rock,Techno,Industrial,Alternative,Ska,Death Metal,Pranks,Soundtrack,Euro-Techno,Ambient,Trip-Hop,Vocal,"
                "Jazz+Funk,Fusion,Trance,Classical,Instrumental,Acid,House,Game,Sound Clip,Gospel,Noise,AlternRock,Bass,"
                "Soul,Punk,Space,Meditative,Instrumental Pop,Instrumental Rock,Ethnic,Gothic,Darkwave,Techno-Industrial,"
                "Electronic,Pop-Folk,Eurodance,Dream,Southern Rock,Comedy,Cult,Gangsta,Top 40,Christian Rap,Pop/Funk,"
                "Jungle,Native American,Cabaret,New Wave,Psychadelic,Rave,Showtunes,Trailer,Lo-Fi,Tribal,Acid Punk,"
                "Acid Jazz,Polka,Retro,Musical,Rock & Roll,Hard Rock").split(",")
VORBIS_KEYS = {"TITLE": "Title", "ARTIST": "Artist", "ALBUM": "Album", "ALBUMARTIST": "Album artist", "TRACKNUMBER": "Track",
               "DISCNUMBER": "Disc", "DATE": "Year", "YEAR": "Year", "GENRE": "Genre", "COMPOSER": "Composer",
               "COMMENT": "Comment", "DESCRIPTION": "Comment", "BPM": "BPM", "PUBLISHER": "Publisher", "ORGANIZATION": "Publisher",
               "COPYRIGHT": "Copyright", "LYRICS": "Lyrics", "ENCODER": "Encoder settings", "ISRC": "ISRC"}
MP4_KEYS = {"\xa9nam": "Title", "\xa9ART": "Artist", "\xa9alb": "Album", "aART": "Album artist", "\xa9day": "Year",
            "\xa9gen": "Genre", "\xa9wrt": "Composer", "\xa9cmt": "Comment", "trkn": "Track", "disk": "Disc",
            "tmpo": "BPM", "cprt": "Copyright", "\xa9too": "Encoder settings", "\xa9lyr": "Lyrics", "desc": "Description",
            "\xa9grp": "Grouping", "cpil": "Compilation", "pgap": "Gapless album", "\xa9st3": "Subtitle"}
MP4_GENRES = ID3V1_GENRES

MPEG_BITRATES = {
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
    (2, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
MPEG_RATES = {1: [44100, 48000, 32000], 2: [22050, 24000, 16000], 3: [11025, 12000, 8000]}   # keyed by version (1, 2, 2.5 -> 3)
AAC_RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350]


class MediaError(Exception):
    pass


def _canon(key):
    return key


def _md5_file(f, start, end, chunk=1 << 20):
    h = hashlib.md5()
    f.seek(start)
    left = end - start
    while left > 0:
        b = f.read(min(chunk, left))
        if not b:
            break
        h.update(b)
        left -= len(b)
    return h.hexdigest()


def _dur(sec):
    if sec is None:
        return ""
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}"


def _synchsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _decode_text(enc, data):
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin-1")
    try:
        return data.decode(codec).rstrip("\x00")
    except UnicodeDecodeError:
        return data.decode("latin-1", "replace").rstrip("\x00")


def _split_term(data, enc):
    """Split at the first terminator for the encoding. Returns (head, tail)."""
    if enc in (1, 2):
        i = 0
        while i + 1 < len(data):
            if data[i] == 0 and data[i + 1] == 0 and i % 2 == 0:
                return data[:i], data[i + 2:]
            i += 1
        return data, b""
    i = data.find(b"\x00")
    return (data, b"") if i < 0 else (data[:i], data[i + 1:])


# ====================================================================== ID3
def read_id3v2(f):
    """Returns (tags list of (name, value), pictures list, total tag size) or ([], [], 0)."""
    f.seek(0)
    head = f.read(10)
    if len(head) < 10 or head[:3] != b"ID3":
        return [], [], 0
    ver, flags = head[3], head[5]
    size = _synchsafe(head[6:10])
    body = f.read(size)
    total = size + 10 + (10 if flags & 0x10 and ver == 4 else 0)
    if flags & 0x80 and ver < 4:                 # whole-tag unsynchronisation
        body = body.replace(b"\xff\x00", b"\xff")
    pos = 0
    if flags & 0x40:                             # extended header
        if ver == 3:
            pos = 4 + struct.unpack(">I", body[:4])[0]
        elif ver == 4:
            pos = _synchsafe(body[:4])
    tags, pics = [], []
    idlen = 3 if ver == 2 else 4
    hlen = 6 if ver == 2 else 10
    while pos + hlen <= len(body):
        fid = body[pos:pos + idlen]
        if fid[0] == 0:
            break
        if ver == 2:
            fsize = int.from_bytes(body[pos + 3:pos + 6], "big")
            fflags = 0
        elif ver == 3:
            fsize = struct.unpack(">I", body[pos + 4:pos + 8])[0]
            fflags = struct.unpack(">H", body[pos + 8:pos + 10])[0]
        else:
            fsize = _synchsafe(body[pos + 4:pos + 8])
            fflags = struct.unpack(">H", body[pos + 8:pos + 10])[0]
        data = body[pos + hlen:pos + hlen + fsize]
        pos += hlen + fsize
        fid = fid.decode("latin-1", "replace")
        if ver == 4 and fflags & 0x02:
            data = data.replace(b"\xff\x00", b"\xff")
        if ver >= 3 and fflags & (0x0080 if ver == 3 else 0x0008):       # compressed: skip
            continue
        if not data:
            continue
        try:
            if fid in ("APIC", "PIC"):
                enc = data[0]
                rest = data[1:]
                if fid == "PIC":
                    mime, rest = rest[:3].decode("latin-1"), rest[3:]
                else:
                    m, rest = _split_term(rest, 0)
                    mime = m.decode("latin-1")
                ptype = rest[0]
                _, img = _split_term(rest[1:], enc)
                pics.append((f"{mime} type {ptype}", len(img), hashlib.md5(img).hexdigest()))
            elif fid in ("COMM", "COM", "USLT", "ULT"):
                enc = data[0]
                text = data[4:]
                desc, val = _split_term(text, enc)
                label = ID3_FRAMES.get(fid, fid)
                d = _decode_text(enc, desc)
                tags.append((label + (f" ({d})" if d else ""), _decode_text(enc, val)))
            elif fid in ("TXXX", "TXX"):
                enc = data[0]
                desc, val = _split_term(data[1:], enc)
                tags.append((f"Custom: {_decode_text(enc, desc)}", _decode_text(enc, val)))
            elif fid[0] == "T":
                txt = _decode_text(data[0], data[1:])
                txt = txt.replace("\x00", " / ")
                if fid in ("TCON", "TCO") and txt.startswith("(") and ")" in txt:
                    n = txt[1:txt.index(")")]
                    if n.isdigit() and int(n) < len(ID3V1_GENRES):
                        txt = ID3V1_GENRES[int(n)] + txt[txt.index(")") + 1:]
                tags.append((ID3_FRAMES.get(fid, f"Frame {fid}"), txt))
            elif fid[0] == "W":
                tags.append((f"Link {fid}", data.split(b"\x00")[0].decode("latin-1")))
            else:
                tags.append((f"Frame {fid}", f"{len(data)} bytes"))
        except (IndexError, ValueError):
            continue
    return tags, pics, total


def read_id3v1(f, size):
    if size < 128:
        return []
    f.seek(size - 128)
    b = f.read(128)
    if b[:3] != b"TAG":
        return []
    g = lambda x: x.split(b"\x00")[0].decode("latin-1").strip()
    tags = [("Title", g(b[3:33])), ("Artist", g(b[33:63])), ("Album", g(b[63:93])), ("Year", g(b[93:97]))]
    if b[125] == 0 and b[126]:
        tags.append(("Comment", g(b[97:125])))
        tags.append(("Track", str(b[126])))
    else:
        tags.append(("Comment", g(b[97:127])))
    gi = b[127]
    if gi < len(ID3V1_GENRES):
        tags.append(("Genre", ID3V1_GENRES[gi]))
    return [(k, v) for k, v in tags if v]


def _mpeg_header(b):
    if len(b) < 4 or b[0] != 0xFF or (b[1] & 0xE0) != 0xE0:
        return None
    ver_bits, layer_bits = (b[1] >> 3) & 3, (b[1] >> 1) & 3
    if ver_bits == 1 or layer_bits == 0:
        return None
    version = {3: 1, 2: 2, 0: 3}[ver_bits]
    layer = 4 - layer_bits
    br_idx, sr_idx = (b[2] >> 4) & 15, (b[2] >> 2) & 3
    if br_idx in (0, 15) or sr_idx == 3:
        return None
    table = MPEG_BITRATES[(1 if version == 1 else 2, layer)]
    bitrate = table[br_idx] * 1000
    rate = MPEG_RATES[version][sr_idx]
    padding = (b[2] >> 1) & 1
    channels = 1 if (b[3] >> 6) == 3 else 2
    samples = 384 if layer == 1 else (1152 if (layer == 2 or version == 1) else 576)
    if layer == 1:
        flen = (12 * bitrate // rate + padding) * 4
    else:
        flen = (144 if samples == 1152 else 72) * bitrate // rate + padding
    return dict(version=version, layer=layer, bitrate=bitrate, rate=rate, channels=channels, samples=samples, frame_len=flen)


def read_mp3(path):
    size = os.path.getsize(path)
    props, tags, pics = [], [], []
    with open(path, "rb") as f:
        t2, pics, off = read_id3v2(f)
        t1 = read_id3v1(f, size)
        end = size - 128 if t1 else size
        tags = t2 + [("ID3v1 " + k, v) for k, v in t1]
        f.seek(off)
        chunk = f.read(65536)
        i = 0
        hdr = None
        while i + 4 <= len(chunk):                    # find the first valid frame
            if chunk[i] == 0xFF:
                hdr = _mpeg_header(chunk[i:i + 4])
                if hdr:
                    break
            i += 1
        audio_start = off + (i if hdr else 0)
        if hdr:
            vname = {1: "MPEG-1", 2: "MPEG-2", 3: "MPEG-2.5"}[hdr["version"]]
            props.append(("Format", f"MP3 ({vname} Layer {hdr['layer']})"))
            props.append(("Sample rate", f"{hdr['rate']} Hz"))
            props.append(("Channels", str(hdr["channels"])))
            body = chunk[i:i + 4 + 40]
            side = (17 if hdr["version"] == 1 and hdr["channels"] == 2 else 9 if hdr["version"] == 1 else 17 if hdr["channels"] == 2 else 9)
            xing = chunk[i + 4 + side:i + 4 + side + 12]
            frames = None
            if xing[:4] in (b"Xing", b"Info"):
                if xing[7] & 1 or xing[4 + 3] & 1:
                    frames = struct.unpack(">I", xing[8:12])[0]
                vbr = xing[:4] == b"Xing"
            else:
                vi = chunk[i + 4 + 32:i + 4 + 32 + 18]
                vbr = vi[:4] == b"VBRI"
                if vbr:
                    frames = struct.unpack(">I", vi[14:18])[0]
            if frames:
                secs = frames * hdr["samples"] / hdr["rate"]
                props.append(("Bitrate", ("VBR, average " if vbr else "") + f"{int((end - audio_start) * 8 / max(secs, 0.001) / 1000)} kbps"))
            else:
                secs = (end - audio_start) * 8 / hdr["bitrate"]
                props.append(("Bitrate", f"{hdr['bitrate'] // 1000} kbps (constant)"))
            props.append(("Duration", _dur(secs)))
        else:
            props.append(("Format", "MP3 (no MPEG frame found)"))
        props.append(("Audio data size", f"{max(0, end - audio_start):,} bytes"))
        audio = _md5_file(f, audio_start, end)
    return dict(props=props, tags=tags, pics=pics, audio=audio)


# ===================================================================== FLAC
def read_flac(path):
    props, tags, pics = [], [], []
    with open(path, "rb") as f:
        if f.read(4) != b"fLaC":
            id3, _, off = read_id3v2(f)
            f.seek(off)
            if f.read(4) != b"fLaC":
                raise MediaError("Not a FLAC file")
        md5sig = ""
        while True:
            h = f.read(4)
            if len(h) < 4:
                break
            last, typ = bool(h[0] & 0x80), h[0] & 0x7F
            n = int.from_bytes(h[1:4], "big")
            blk = f.read(n)
            if typ == 0 and len(blk) >= 34:
                v = int.from_bytes(blk[10:18], "big")
                rate = v >> 44
                ch = ((v >> 41) & 7) + 1
                bps = ((v >> 36) & 31) + 1
                total = v & ((1 << 36) - 1)
                props.append(("Format", "FLAC"))
                props.append(("Sample rate", f"{rate} Hz"))
                props.append(("Channels", str(ch)))
                props.append(("Bits per sample", str(bps)))
                props.append(("Duration", _dur(total / rate) if rate and total else "unknown"))
                md5sig = blk[18:34].hex()
            elif typ == 4:
                vl = struct.unpack("<I", blk[:4])[0]
                vendor = blk[4:4 + vl].decode("utf-8", "replace")
                p = 4 + vl
                cnt = struct.unpack("<I", blk[p:p + 4])[0]
                p += 4
                tags.append(("Vendor", vendor))
                for _ in range(cnt):
                    if p + 4 > len(blk):
                        break
                    cl = struct.unpack("<I", blk[p:p + 4])[0]
                    c = blk[p + 4:p + 4 + cl].decode("utf-8", "replace")
                    p += 4 + cl
                    if "=" in c:
                        k, val = c.split("=", 1)
                        tags.append((VORBIS_KEYS.get(k.upper(), k.upper()), val))
            elif typ == 6:
                ptype = struct.unpack(">I", blk[:4])[0]
                ml = struct.unpack(">I", blk[4:8])[0]
                mime = blk[8:8 + ml].decode("latin-1")
                q = 8 + ml
                dl = struct.unpack(">I", blk[q:q + 4])[0]
                q += 4 + dl + 16
                dlen = struct.unpack(">I", blk[q:q + 4])[0]
                img = blk[q + 4:q + 4 + dlen]
                pics.append((f"{mime} type {ptype}", len(img), hashlib.md5(img).hexdigest()))
            if last:
                break
        start = f.tell()
        size = os.path.getsize(path)
        props.append(("Audio data size", f"{size - start:,} bytes"))
        if md5sig:
            props.append(("Decoded-audio MD5 (from header)", md5sig))
        audio = _md5_file(f, start, size)
    return dict(props=props, tags=tags, pics=pics, audio=audio)


# ===================================================================== MP4
def _atoms(f, start, end):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        h = f.read(8)
        if len(h) < 8:
            return
        size, typ = struct.unpack(">I4s", h)
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        yield typ.decode("latin-1"), pos + hdr, min(pos + size, end)
        pos += size


def _find(f, start, end, path):
    cur = [(start, end)]
    for name in path:
        nxt = []
        for s, e in cur:
            for typ, ps, pe in _atoms(f, s, e):
                if typ == name:
                    nxt.append((ps, pe))
        cur = nxt
        if not cur:
            return []
    return cur


def read_mp4(path):
    size = os.path.getsize(path)
    props, tags, pics = [], [], []
    with open(path, "rb") as f:
        top = list(_atoms(f, 0, size))
        names = [t for t, _, _ in top]
        if "ftyp" not in names and "moov" not in names:
            raise MediaError("Not an MP4 / M4A file")
        for t, s, e in top:
            if t == "ftyp":
                f.seek(s)
                props.append(("Format", "MP4 container (" + f.read(4).decode("latin-1").strip() + ")"))
        for s, e in _find(f, 0, size, ["moov", "mvhd"]):
            f.seek(s)
            ver = f.read(1)[0]
            f.read(3)
            if ver == 1:
                f.read(16)
                ts, dur = struct.unpack(">IQ", f.read(12))
            else:
                f.read(8)
                ts, dur = struct.unpack(">II", f.read(8))
            props.append(("Duration", _dur(dur / ts) if ts else ""))
        tcount = 0
        for ts_, te_ in _find(f, 0, size, ["moov", "trak"]):
            tcount += 1
            hd = _find(f, ts_, te_, ["mdia", "hdlr"])
            handler = ""
            if hd:
                f.seek(hd[0][0] + 8)
                handler = f.read(4).decode("latin-1")
            kind = {"soun": "Audio", "vide": "Video", "text": "Text", "sbtl": "Subtitles", "subt": "Subtitles"}.get(handler, handler)
            desc = []
            for ss, se in _find(f, ts_, te_, ["mdia", "minf", "stbl", "stsd"]):
                f.seek(ss + 8)
                h = f.read(8)
                if len(h) == 8:
                    codec = h[4:8].decode("latin-1")
                    desc.append(codec)
                    body = f.read(28)
                    if handler == "soun" and len(body) >= 28:
                        ch, bits = struct.unpack(">HH", body[16:20])
                        rate = struct.unpack(">I", body[24:28])[0] >> 16
                        desc.append(f"{ch} ch, {rate} Hz")
                    elif handler == "vide" and len(body) >= 28:
                        w, hh = struct.unpack(">HH", body[24:28])
                        desc.append(f"{w}x{hh}")
            props.append((f"Track {tcount}", kind + (": " + ", ".join(desc) if desc else "")))
        for ls, le in _find(f, 0, size, ["moov", "udta", "meta", "ilst"]) or _find_meta(f, size):
            for typ, ps, pe in _atoms(f, ls, le):
                key = MP4_KEYS.get(typ, typ.replace("\xa9", "(c)"))
                for dt, dps, dpe in _atoms(f, ps, pe):
                    if dt != "data":
                        continue
                    f.seek(dps)
                    dh = f.read(8)
                    flags = int.from_bytes(dh[1:4], "big")
                    val = f.read(dpe - dps - 8)
                    if typ == "covr":
                        pics.append((f"image flag {flags}", len(val), hashlib.md5(val).hexdigest()))
                    elif typ in ("trkn", "disk") and len(val) >= 6:
                        n, tot = struct.unpack(">HH", val[2:6])
                        tags.append((key, f"{n}/{tot}" if tot else str(n)))
                    elif typ == "gnre" and len(val) >= 2:
                        g = struct.unpack(">H", val[:2])[0]
                        tags.append(("Genre", MP4_GENRES[g - 1] if 0 < g <= len(MP4_GENRES) else str(g)))
                    elif flags == 1 or flags == 0 and typ.startswith("\xa9"):
                        tags.append((key, val.decode("utf-8", "replace")))
                    elif flags == 21:
                        tags.append((key, str(int.from_bytes(val, "big", signed=True))))
                    elif typ in ("cpil", "pgap") and val:
                        tags.append((key, "Yes" if val[0] else "No"))
                    else:
                        tags.append((key, f"{len(val)} bytes"))
        h = hashlib.md5()
        total = 0
        for t, s, e in top:
            if t == "mdat":
                f.seek(s)
                left = e - s
                total += left
                while left > 0:
                    b = f.read(min(1 << 20, left))
                    if not b:
                        break
                    h.update(b)
                    left -= len(b)
        props.append(("Audio/video data size", f"{total:,} bytes"))
    return dict(props=props, tags=tags, pics=pics, audio=h.hexdigest())


def _find_meta(f, size):
    """moov/udta/meta is a 'full box' (4 extra version bytes before its children)."""
    out = []
    for us, ue in _find(f, 0, size, ["moov", "udta"]):
        for typ, ps, pe in _atoms(f, us, ue):
            if typ == "meta":
                for t2, s2, e2 in _atoms(f, ps + 4, pe):
                    if t2 == "ilst":
                        out.append((s2, e2))
    return out


# ===================================================================== ADTS
def read_aac(path):
    size = os.path.getsize(path)
    props, tags, pics = [], [], []
    with open(path, "rb") as f:
        tags, pics, off = read_id3v2(f)
        f.seek(off)
        b = f.read(7)
        if len(b) == 7 and b[0] == 0xFF and (b[1] & 0xF0) == 0xF0:
            prof = (b[2] >> 6) + 1
            sr = AAC_RATES[(b[2] >> 2) & 15] if ((b[2] >> 2) & 15) < len(AAC_RATES) else 0
            ch = ((b[2] & 1) << 2) | (b[3] >> 6)
            props += [("Format", "AAC (ADTS)"), ("AAC profile", {1: "Main", 2: "LC", 3: "SSR", 4: "LTP"}.get(prof, str(prof))),
                      ("Sample rate", f"{sr} Hz"), ("Channels", str(ch))]
        else:
            props.append(("Format", "AAC (no ADTS header found)"))
        props.append(("Audio data size", f"{size - off:,} bytes"))
        audio = _md5_file(f, off, size)
    return dict(props=props, tags=tags, pics=pics, audio=audio)


# ===================================================================== API
def sniff(path):
    with open(path, "rb") as f:
        head = f.read(12)
    ext = os.path.splitext(path)[1].lower()
    if head[:4] == b"fLaC" or ext == ".flac":
        return "flac"
    if head[4:8] == b"ftyp" or ext in (".m4a", ".mp4", ".m4b", ".m4v", ".mov"):
        return "mp4"
    if ext == ".aac":
        return "aac"
    return "mp3"


def read_media(path):
    kind = sniff(path)
    fn = {"flac": read_flac, "mp4": read_mp4, "aac": read_aac, "mp3": read_mp3}[kind]
    try:
        info = fn(path)
    except (OSError, struct.error, IndexError) as e:
        raise MediaError(f"Cannot read {os.path.basename(path)}: {e}") from e
    info["kind"] = kind
    return info


def rows_for(path):
    """Flat (group, name, value) rows for the property comparison view."""
    info = read_media(path)
    rows = [("File", "Size", f"{os.path.getsize(path):,} bytes")]
    rows += [("Audio", k, v) for k, v in info["props"]]
    seen = {}
    for k, v in info["tags"]:
        n = seen.get(k, 0)
        seen[k] = n + 1
        rows.append(("Tags", k if n == 0 else f"{k} ({n + 1})", v))
    for i, (desc, size, md5) in enumerate(info["pics"], 1):
        rows.append(("Pictures", f"Picture {i}", f"{desc}, {size:,} bytes, md5 {md5}"))
    rows.append(("Audio data", "Fingerprint (MD5 of the audio bytes, tags excluded)", info["audio"]))
    return rows
