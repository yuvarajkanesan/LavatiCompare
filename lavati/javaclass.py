"""Read a Java .class file and print it like `javap -p -c`: class header, fields, methods and bytecode. Pure Python, no Qt.

So two compiled classes can be compared as text (what changed in the signatures and in the bytecode) with no JDK installed."""
import struct

OPS = ("nop aconst_null iconst_m1 iconst_0 iconst_1 iconst_2 iconst_3 iconst_4 iconst_5 lconst_0 lconst_1 fconst_0 fconst_1 fconst_2 "
       "dconst_0 dconst_1 bipush sipush ldc ldc_w ldc2_w iload lload fload dload aload iload_0 iload_1 iload_2 iload_3 lload_0 lload_1 "
       "lload_2 lload_3 fload_0 fload_1 fload_2 fload_3 dload_0 dload_1 dload_2 dload_3 aload_0 aload_1 aload_2 aload_3 iaload laload "
       "faload daload aaload baload caload saload istore lstore fstore dstore astore istore_0 istore_1 istore_2 istore_3 lstore_0 "
       "lstore_1 lstore_2 lstore_3 fstore_0 fstore_1 fstore_2 fstore_3 dstore_0 dstore_1 dstore_2 dstore_3 astore_0 astore_1 astore_2 "
       "astore_3 iastore lastore fastore dastore aastore bastore castore sastore pop pop2 dup dup_x1 dup_x2 dup2 dup2_x1 dup2_x2 swap "
       "iadd ladd fadd dadd isub lsub fsub dsub imul lmul fmul dmul idiv ldiv fdiv ddiv irem lrem frem drem ineg lneg fneg dneg ishl "
       "lshl ishr lshr iushr lushr iand land ior lor ixor lxor iinc i2l i2f i2d l2i l2f l2d f2i f2l f2d d2i d2l d2f i2b i2c i2s lcmp "
       "fcmpl fcmpg dcmpl dcmpg ifeq ifne iflt ifge ifgt ifle if_icmpeq if_icmpne if_icmplt if_icmpge if_icmpgt if_icmple if_acmpeq "
       "if_acmpne goto jsr ret tableswitch lookupswitch ireturn lreturn freturn dreturn areturn return getstatic putstatic getfield "
       "putfield invokevirtual invokespecial invokestatic invokeinterface invokedynamic new newarray anewarray arraylength athrow "
       "checkcast instanceof monitorenter monitorexit wide multianewarray ifnull ifnonnull goto_w jsr_w").split()

LOCAL1 = {"iload", "lload", "fload", "dload", "aload", "istore", "lstore", "fstore", "dstore", "astore", "ret"}
CP1 = {"ldc"}
CP2 = {"ldc_w", "ldc2_w", "getstatic", "putstatic", "getfield", "putfield", "invokevirtual", "invokespecial", "invokestatic", "new",
       "anewarray", "checkcast", "instanceof"}
BRANCH = {"ifeq", "ifne", "iflt", "ifge", "ifgt", "ifle", "if_icmpeq", "if_icmpne", "if_icmplt", "if_icmpge", "if_icmpgt", "if_icmple",
          "if_acmpeq", "if_acmpne", "goto", "jsr", "ifnull", "ifnonnull"}
NEWARRAY = {4: "boolean", 5: "char", 6: "float", 7: "double", 8: "byte", 9: "short", 10: "int", 11: "long"}
PRIMS = {"B": "byte", "C": "char", "D": "double", "F": "float", "I": "int", "J": "long", "S": "short", "Z": "boolean", "V": "void"}
MAJOR = {45: "1.1", 46: "1.2", 47: "1.3", 48: "1.4", 49: "5", 50: "6", 51: "7", 52: "8", 53: "9", 54: "10", 55: "11", 56: "12", 57: "13",
         58: "14", 59: "15", 60: "16", 61: "17", 62: "18", 63: "19", 64: "20", 65: "21", 66: "22", 67: "23", 68: "24", 69: "25"}


class ClassError(Exception):
    pass


class _R:
    def __init__(self, data):
        self.d, self.p = data, 0

    def take(self, fmt):
        n = struct.calcsize(fmt)
        if self.p + n > len(self.d):
            raise ClassError("The file ends too early to be a class file")
        v = struct.unpack_from(fmt, self.d, self.p)
        self.p += n
        return v if len(v) > 1 else v[0]

    def bytes(self, n):
        if self.p + n > len(self.d):
            raise ClassError("The file ends too early to be a class file")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b


def _mutf8(b):
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("utf-8", "replace")


def parse(data):
    r = _R(data)
    if r.take(">I") != 0xCAFEBABE:
        raise ClassError("Not a Java class file (missing the CAFEBABE marker)")
    minor, major = r.take(">HH")
    n = r.take(">H")
    cp = [None] * n
    i = 1
    while i < n:
        tag = r.take(">B")
        if tag == 1:
            cp[i] = ("Utf8", _mutf8(r.bytes(r.take(">H"))))
        elif tag == 3:
            cp[i] = ("Integer", r.take(">i"))
        elif tag == 4:
            cp[i] = ("Float", r.take(">f"))
        elif tag == 5:
            cp[i] = ("Long", r.take(">q"))
            i += 1
        elif tag == 6:
            cp[i] = ("Double", r.take(">d"))
            i += 1
        elif tag in (7, 8, 16, 19, 20):
            cp[i] = ({7: "Class", 8: "String", 16: "MethodType", 19: "Module", 20: "Package"}[tag], r.take(">H"))
        elif tag in (9, 10, 11, 12, 17, 18):
            cp[i] = ({9: "Fieldref", 10: "Methodref", 11: "InterfaceMethodref", 12: "NameAndType", 17: "Dynamic", 18: "InvokeDynamic"}[tag],
                     *r.take(">HH"))
        elif tag == 15:
            cp[i] = ("MethodHandle", r.take(">B"), r.take(">H"))
        else:
            raise ClassError(f"Unknown constant pool entry {tag}")
        i += 1
    access, this, sup = r.take(">HHH")
    ifaces = [r.take(">H") for _ in range(r.take(">H"))]

    def attrs():
        out = []
        for _ in range(r.take(">H")):
            name = r.take(">H")
            out.append((name, r.bytes(r.take(">I"))))
        return out
    fields = [(*r.take(">HHH"), attrs()) for _ in range(r.take(">H"))]
    methods = [(*r.take(">HHH"), attrs()) for _ in range(r.take(">H"))]
    return dict(minor=minor, major=major, cp=cp, access=access, this=this, super=sup, ifaces=ifaces, fields=fields, methods=methods,
                attrs=attrs())


class _Pool:
    def __init__(self, cp):
        self.cp = cp

    def utf(self, i):
        e = self.cp[i] if 0 < i < len(self.cp) else None
        return e[1] if e and e[0] == "Utf8" else f"#{i}"

    def cls(self, i):
        e = self.cp[i] if 0 < i < len(self.cp) else None
        return self.utf(e[1]).replace("/", ".") if e and e[0] == "Class" else f"#{i}"

    def nat(self, i):
        e = self.cp[i]
        return f"{self.utf(e[1])}:{self.utf(e[2])}"

    def show(self, i):
        """The comment javap prints after a constant-pool operand."""
        if not (0 < i < len(self.cp)) or self.cp[i] is None:
            return f"#{i}"
        e = self.cp[i]
        k = e[0]
        if k == "Class":
            return f"class {self.cls(i)}"
        if k in ("Fieldref", "Methodref", "InterfaceMethodref"):
            label = {"Fieldref": "Field", "Methodref": "Method", "InterfaceMethodref": "InterfaceMethod"}[k]
            return f"{label} {self.cls(e[1])}.{self.nat(e[2])}"
        if k == "String":
            return "String " + repr(self.utf(e[1]))[1:-1].join('""')
        if k in ("Integer", "Float", "Long", "Double"):
            return f"{k} {e[1]}"
        if k == "InvokeDynamic":
            return f"InvokeDynamic #{e[1]}:{self.nat(e[2])}"
        if k == "NameAndType":
            return self.nat(i)
        return k


def _type(desc, i=0):
    """Java type for the descriptor starting at desc[i]; returns (text, next index)."""
    dims = 0
    while i < len(desc) and desc[i] == "[":
        dims += 1
        i += 1
    if i >= len(desc):
        return "?", i
    if desc[i] == "L":
        j = desc.index(";", i)
        t = desc[i + 1:j].replace("/", ".")
        i = j + 1
    else:
        t = PRIMS.get(desc[i], "?")
        i += 1
    return t + "[]" * dims, i


def method_sig(desc):
    """'(ILjava/lang/String;)V' -> ('void', ['int', 'java.lang.String'])"""
    try:
        i, args = 1, []
        while desc[i] != ")":
            t, i = _type(desc, i)
            args.append(t)
        ret, _ = _type(desc, i + 1)
        return ret, args
    except (IndexError, ValueError):
        return "?", []


CLASS_FLAGS = [(0x0001, "public"), (0x0010, "final"), (0x0400, "abstract")]
FIELD_FLAGS = [(0x0001, "public"), (0x0002, "private"), (0x0004, "protected"), (0x0008, "static"), (0x0010, "final"), (0x0040, "volatile"),
               (0x0080, "transient")]
METHOD_FLAGS = [(0x0001, "public"), (0x0002, "private"), (0x0004, "protected"), (0x0008, "static"), (0x0010, "final"),
                (0x0020, "synchronized"), (0x0100, "native"), (0x0400, "abstract")]


def _flags(v, table):
    return " ".join(n for bit, n in table if v & bit)


def _code(code, pool, out, indent):
    """Disassemble the bytes of a Code attribute body into lines."""
    r = _R(code)
    stack, maxloc = r.take(">HH")
    ln = r.take(">I")
    b = r.bytes(ln)
    out.append(f"{indent}Code: stack={stack}, locals={maxloc}")
    p = 0
    while p < len(b):
        op = b[p]
        name = OPS[op] if op < len(OPS) else f"op_{op}"
        start, p = p, p + 1
        text = name
        try:
            if name in LOCAL1:
                text += f" {b[p]}"
                p += 1
            elif name in ("bipush",):
                text += f" {struct.unpack_from('>b', b, p)[0]}"
                p += 1
            elif name == "sipush":
                text += f" {struct.unpack_from('>h', b, p)[0]}"
                p += 2
            elif name in CP1:
                text += f" #{b[p]}   // {pool.show(b[p])}"
                p += 1
            elif name in CP2:
                idx = struct.unpack_from(">H", b, p)[0]
                text += f" #{idx}   // {pool.show(idx)}"
                p += 2
            elif name in ("invokeinterface", "invokedynamic"):
                idx = struct.unpack_from(">H", b, p)[0]
                text += f" #{idx}   // {pool.show(idx)}"
                p += 4
            elif name == "multianewarray":
                idx = struct.unpack_from(">H", b, p)[0]
                text += f" #{idx}, {b[p + 2]}   // {pool.show(idx)}"
                p += 3
            elif name == "newarray":
                text += f" {NEWARRAY.get(b[p], b[p])}"
                p += 1
            elif name == "iinc":
                text += f" {b[p]}, {struct.unpack_from('>b', b, p + 1)[0]}"
                p += 2
            elif name in BRANCH:
                off = struct.unpack_from(">h", b, p)[0]
                text += f" {start + off}"
                p += 2
            elif name in ("goto_w", "jsr_w"):
                text += f" {start + struct.unpack_from('>i', b, p)[0]}"
                p += 4
            elif name == "wide":
                inner = OPS[b[p]] if b[p] < len(OPS) else "?"
                if inner == "iinc":
                    text += f" iinc {struct.unpack_from('>H', b, p + 1)[0]}, {struct.unpack_from('>h', b, p + 3)[0]}"
                    p += 5
                else:
                    text += f" {inner} {struct.unpack_from('>H', b, p + 1)[0]}"
                    p += 3
            elif name == "tableswitch":
                p = (p + 3) & ~3
                default, lo, hi = struct.unpack_from(">iii", b, p)
                p += 12
                offs = struct.unpack_from(f">{hi - lo + 1}i", b, p)
                p += 4 * (hi - lo + 1)
                text += " { // " + f"{lo} to {hi}"
                out.append(f"{indent}  {start:>4}: {text}")
                for k, o in enumerate(offs):
                    out.append(f"{indent}            {lo + k}: {start + o}")
                out.append(f"{indent}      default: {start + default}")
                out.append(f"{indent}       }}")
                continue
            elif name == "lookupswitch":
                p = (p + 3) & ~3
                default, npairs = struct.unpack_from(">ii", b, p)
                p += 8
                out.append(f"{indent}  {start:>4}: lookupswitch {{ // {npairs}")
                for _ in range(npairs):
                    key, o = struct.unpack_from(">ii", b, p)
                    p += 8
                    out.append(f"{indent}            {key}: {start + o}")
                out.append(f"{indent}      default: {start + default}")
                out.append(f"{indent}       }}")
                continue
        except (struct.error, IndexError):
            out.append(f"{indent}  {start:>4}: {name} (truncated)")
            break
        out.append(f"{indent}  {start:>4}: {text}")
    exc = r.take(">H")
    if exc:
        out.append(f"{indent}Exception table:  from  to  target  type")
        for _ in range(exc):
            s, e, h, t = r.take(">HHHH")
            out.append(f"{indent}                  {s:>4} {e:>3} {h:>6}   {pool.cls(t) if t else 'any'}")


def disassemble_bytes(data):
    c = parse(data)
    pool = _Pool(c["cp"])
    out = []
    ver = MAJOR.get(c["major"], "?")
    out.append(f"// class file version {c['major']}.{c['minor']} (Java {ver})")
    name = pool.cls(c["this"])
    sup = pool.cls(c["super"]) if c["super"] else ""
    acc = c["access"]
    kind = "interface" if acc & 0x0200 else ("enum" if acc & 0x4000 else ("@interface" if acc & 0x2000 else "class"))
    fl = _flags(acc & ~0x0200 if kind != "class" else acc, CLASS_FLAGS)
    if kind == "interface":
        fl = fl.replace("abstract", "").strip()
    head = " ".join(x for x in (fl, kind, name) if x)
    if sup and sup != "java.lang.Object" and kind == "class":
        head += f" extends {sup}"
    if c["ifaces"]:
        head += (" extends " if kind == "interface" else " implements ") + ", ".join(pool.cls(i) for i in c["ifaces"])
    for nm, body in c["attrs"]:
        if pool.utf(nm) == "SourceFile":
            out.append(f'// source file: {pool.utf(struct.unpack(">H", body)[0])}')
        elif pool.utf(nm) == "Signature":
            out.append(f'// generic signature: {pool.utf(struct.unpack(">H", body)[0])}')
    out.append(head + " {")
    out.append(f"  // constant pool: {len(c['cp']) - 1} entries")
    if c["fields"]:
        out.append("")
    for acc_f, nm, desc, at in c["fields"]:
        t, _ = _type(pool.utf(desc))
        line = " ".join(x for x in (_flags(acc_f, FIELD_FLAGS), t, pool.utf(nm)) if x)
        for an, body in at:
            if pool.utf(an) == "ConstantValue":
                line += " = " + pool.show(struct.unpack(">H", body)[0]).split(" ", 1)[-1]
            elif pool.utf(an) == "Signature":
                line += f"   // signature {pool.utf(struct.unpack('>H', body)[0])}"
        out.append(f"  {line};")
    for acc_m, nm, desc, at in c["methods"]:
        mname = pool.utf(nm)
        ret, args = method_sig(pool.utf(desc))
        if mname == "<clinit>":
            sig = "static {}"
        elif mname == "<init>":
            sig = f"{name.rsplit('.', 1)[-1]}({', '.join(args)})"
        else:
            sig = f"{ret} {mname}({', '.join(args)})"
        line = " ".join(x for x in (_flags(acc_m, METHOD_FLAGS), sig) if x)
        out.append("")
        throws = ""
        for an, body in at:
            if pool.utf(an) == "Exceptions":
                k = struct.unpack_from(">H", body, 0)[0]
                throws = " throws " + ", ".join(pool.cls(struct.unpack_from(">H", body, 2 + 2 * j)[0]) for j in range(k))
        out.append(f"  {line}{throws};")
        out.append(f"    descriptor: {pool.utf(desc)}")
        for an, body in at:
            if pool.utf(an) == "Code":
                try:
                    _code(body, pool, out, "    ")
                except (ClassError, struct.error) as e:
                    out.append(f"    Code: (unreadable: {e})")
    out.append("}")
    inner = [pool.utf(an) for an, _ in c["attrs"]]
    if "InnerClasses" in inner:
        out.append("// has inner classes")
    return "\n".join(out) + "\n"


def disassemble(path):
    with open(path, "rb") as f:
        data = f.read(64 * 1024 * 1024)
    return disassemble_bytes(data)
