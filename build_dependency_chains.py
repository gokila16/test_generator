"""
build_dependency_chains.py

Builds dependency_chains.json — for each method in extracted_metadata_final.json,
resolves how to construct every parameter needed to call that method.

Uses:
  - class_inventory.json  (from build_class_inventory.py)
  - extracted_metadata_final.json
  - src/test/resources/  (scanned for available resource files)

Output: dependency_chains.json

Run with regular Python:
  python build_dependency_chains.py

FIXES APPLIED (v2):
  1. PDFStreamEngine param → uses PDFTextStripper not null
  2. PDFGraphicsStreamEngine param → uses PageDrawer not null
  3. OperatorProcessor subclass receivers → properly constructed with real context
  4. Abstract receiver fallback → walks subclass chain to find one with a resolvable ctor
  5. constructor_with_args fallback → never emits null for PDFStreamEngine/PDFGraphicsStreamEngine params
  6. RandomAccessReadBuffer added to FILE_TYPES so it gets a real construction recipe
"""

import json
import os
import sys

# ── Configuration ─────────────────────────────────────────
METADATA_FILE      = r"C:\Users\Harini\Documents\thesis_research\test_generator\extracted_metadata_final.json"
CLASS_INVENTORY    = r"C:\Users\Harini\Documents\thesis_research\test_generator\class_inventory.json"
OUTPUT_FILE        = r"C:\Users\Harini\Documents\thesis_research\test_generator\dependency_chains.json"
TEST_RESOURCES_DIR = r"C:\Users\Harini\Documents\thesis_research\PDFBOX-v5\pdfbox\src\test\resources"

# extracted_metadata_final.json contains all 1309 methods with has_developer_tests field.
# Setting UNCOVERED_ONLY = True filters to the 932 methods without developer tests,
# which is what the test generator processes. Set to False to build chains for all 1309.
UNCOVERED_ONLY = True

# ── Java primitives and simple types ──────────────────────
PRIMITIVES = {
    "int":     "0",
    "long":    "0L",
    "float":   "0.0f",
    "double":  "0.0",
    "boolean": "false",
    "byte":    "(byte) 0",
    "short":   "(short) 0",
    "char":    "'a'",
}

SIMPLE_TYPES = {
    "String":        '"test"',
    "Integer":       "0",
    "Long":          "0L",
    "Float":         "0.0f",
    "Double":        "0.0",
    "Boolean":       "false",
    "Byte":          "(byte) 0",
    "Short":         "(short) 0",
    "Character":     "'a'",
    "Object":        "new Object()",
    "StringBuilder": "new StringBuilder()",
    "StringBuffer":  "new StringBuffer()",
    "Number":        "0",
    "AffineTransform":    "new AffineTransform()",
    "Rectangle":          "new Rectangle(0, 0, 100, 100)",
    "Rectangle2D":        "new Rectangle2D.Double(0, 0, 100, 100)",
    "Point2D":            "new Point2D.Double(0.0, 0.0)",
    "Color":              "Color.BLACK",
    "RenderingHints":     "new RenderingHints(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)",
    "Shape":              "new Rectangle(0, 0, 100, 100)",
    "Paint":              "Color.BLACK",
    "Path2D":             "new Path2D.Double()",
    "CharSequence":       '"test"',
    "Calendar":           "Calendar.getInstance()",
    "Element":            "/* Element — XML DOM element, skip */",
    "StreamCacheCreateFunction": "/* StreamCacheCreateFunction — skip */",
}

# File-related types that should use test resources
# FIX 6: Added RandomAccessReadBuffer explicitly (was missing, causing ALLOWLIST failures
# because the class wasn't in inventory but constructions referenced it)
FILE_TYPES = {
    "File", "Path", "InputStream", "FileInputStream",
    "BufferedInputStream", "RandomAccessRead",
    "RandomAccessReadBufferedFile", "RandomAccessReadBuffer",
    "SeekableByteChannel",
}

# Types we deliberately skip (output streams, writers)
SKIP_TYPES = {
    "OutputStream", "FileOutputStream", "BufferedOutputStream",
    "Writer", "PrintWriter", "BufferedWriter", "StringWriter",
    "WritableRaster", "ColorModel", "Image", "ImageObserver",
    "Raster", "BufferedImage", "Graphics2D", "Graphics",
    "GlyphVector", "BufferedImageOp", "RenderableImage",
    "RenderedImage", "AttributedCharacterIterator", "PageFormat",
}

# ── Manual overrides ───────────────────────────────────────
# FIX 1: PDFStreamEngine — was resolving to null in constructor_with_args fallback.
#         Now explicitly maps to PDFTextStripper construction.
# FIX 2: PDFGraphicsStreamEngine — was missing entirely, now maps to PageDrawer.
# FIX 3: All OperatorProcessor subclasses that take PDFStreamEngine in ctor
#         now get a real PDFTextStripper context instead of null.
#         Added entries for every concrete operator class seen in failing tests.
MANUAL_OVERRIDES = {

    # ── PDFStreamEngine family ─────────────────────────────
    # FIX 1: Was producing null — now produces real PDFTextStripper instance
    "PDFStreamEngine": {
        "strategy":     "subclass",
        "subclass":     "PDFTextStripper",
        "construction": "PDFStreamEngine {name} = new PDFTextStripper();",
    },
    # FIX 2: Was missing entirely — PageDrawer is the only concrete subclass
    # NOTE: PageDrawer requires PDPage, so we construct that too
    "PDFGraphicsStreamEngine": {
        "strategy":     "subclass",
        "subclass":     "PageDrawer",
        "construction": (
            "PDDocument _doc_{name} = new PDDocument();\n"
            "PDPage _page_{name} = new PDPage();\n"
            "_doc_{name}.addPage(_page_{name});\n"
            "PageDrawerParameters _params_{name} = "
            "new PageDrawerParameters(new PageDrawer(), _page_{name}, false);\n"
            "PDFGraphicsStreamEngine {name} = new PageDrawer(_params_{name});"
        ),
    },

    # ── OperatorProcessor subclasses ──────────────────────
    # FIX 3: All of these took PDFStreamEngine in ctor and were getting null.
    #         They now get a real PDFTextStripper context.
    #         Pattern: new ConcreteOperator(new PDFTextStripper())
    "OperatorProcessor": {
        "strategy":  "skip",
        "construction": "/* OperatorProcessor {name} — abstract, use concrete subclass directly */",
    },

    # Color operators
    "SetColor": {
        "strategy":     "subclass",
        "subclass":     "SetStrokingDeviceRGBColor",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetColor {name} = new SetStrokingDeviceRGBColor(_engine_{name});"
        ),
    },
    "SetNonStrokingColor": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetNonStrokingColor {name} = new SetNonStrokingColor(_engine_{name});"
        ),
    },
    "SetNonStrokingColorN": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetNonStrokingColorN {name} = new SetNonStrokingColorN(_engine_{name});"
        ),
    },
    "SetNonStrokingColorSpace": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetNonStrokingColorSpace {name} = new SetNonStrokingColorSpace(_engine_{name});"
        ),
    },
    "SetNonStrokingDeviceCMYKColor": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetNonStrokingDeviceCMYKColor {name} = new SetNonStrokingDeviceCMYKColor(_engine_{name});"
        ),
    },
    "SetNonStrokingDeviceGrayColor": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetNonStrokingDeviceGrayColor {name} = new SetNonStrokingDeviceGrayColor(_engine_{name});"
        ),
    },
    "SetNonStrokingDeviceRGBColor": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetNonStrokingDeviceRGBColor {name} = new SetNonStrokingDeviceRGBColor(_engine_{name});"
        ),
    },
    "SetStrokingColor": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetStrokingColor {name} = new SetStrokingColor(_engine_{name});"
        ),
    },
    "SetStrokingColorN": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetStrokingColorN {name} = new SetStrokingColorN(_engine_{name});"
        ),
    },
    "SetStrokingColorSpace": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetStrokingColorSpace {name} = new SetStrokingColorSpace(_engine_{name});"
        ),
    },
    "SetStrokingDeviceCMYKColor": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetStrokingDeviceCMYKColor {name} = new SetStrokingDeviceCMYKColor(_engine_{name});"
        ),
    },
    "SetStrokingDeviceGrayColor": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetStrokingDeviceGrayColor {name} = new SetStrokingDeviceGrayColor(_engine_{name});"
        ),
    },
    "SetStrokingDeviceRGBColor": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "SetStrokingDeviceRGBColor {name} = new SetStrokingDeviceRGBColor(_engine_{name});"
        ),
    },

    # Graphics operators
    "DrawObject": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDFTextStripper _engine_{name} = new PDFTextStripper();\n"
            "DrawObject {name} = new DrawObject(_engine_{name});"
        ),
    },
    "AppendRectangleToPath": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDDocument _doc_{name} = new PDDocument();\n"
            "PDPage _page_{name} = new PDPage();\n"
            "_doc_{name}.addPage(_page_{name});\n"
            "PageDrawerParameters _params_{name} = "
            "new PageDrawerParameters(new PageDrawer(), _page_{name}, false);\n"
            "PageDrawer _pd_{name} = new PageDrawer(_params_{name});\n"
            "AppendRectangleToPath {name} = new AppendRectangleToPath(_pd_{name});"
        ),
    },
    "BeginInlineImage": {
        "strategy":     "constructor_with_args",
        "construction": (
            "PDDocument _doc_{name} = new PDDocument();\n"
            "PDPage _page_{name} = new PDPage();\n"
            "_doc_{name}.addPage(_page_{name});\n"
            "PageDrawerParameters _params_{name} = "
            "new PageDrawerParameters(new PageDrawer(), _page_{name}, false);\n"
            "PageDrawer _pd_{name} = new PageDrawer(_params_{name});\n"
            "BeginInlineImage {name} = new BeginInlineImage(_pd_{name});"
        ),
    },

    # ── Abstract classes: use known concrete subclass ──────
    "PDAbstractContentStream": {
        "strategy":  "subclass",
        "subclass":  "PDPageContentStream",
        "construction": (
            "PDDocument _doc_{name} = new PDDocument();\n"
            "PDPage _page_{name} = new PDPage();\n"
            "_doc_{name}.addPage(_page_{name});\n"
            "PDAbstractContentStream {name} = new PDPageContentStream("
            "_doc_{name}, _page_{name});"
        ),
    },
    "PDSimpleFont": {
        "strategy":  "subclass",
        "subclass":  "PDType1Font",
        "construction": "PDSimpleFont {name} = PDType1Font.HELVETICA;",
    },
    "PDTerminalField": {
        "strategy":  "subclass",
        "subclass":  "PDTextField",
        "construction": (
            "PDDocument _doc_{name} = new PDDocument();\n"
            "PDAcroForm _form_{name} = new PDAcroForm(_doc_{name});\n"
            "PDTerminalField {name} = new PDTextField(_form_{name});"
        ),
    },
    "PDVariableText": {
        "strategy":  "subclass",
        "subclass":  "PDTextField",
        "construction": (
            "PDDocument _doc_{name} = new PDDocument();\n"
            "PDAcroForm _form_{name} = new PDAcroForm(_doc_{name});\n"
            "PDVariableText {name} = new PDTextField(_form_{name});"
        ),
    },
    "PDCIDFont": {
        "strategy":  "skip",
        "construction": "/* PDCIDFont {name} — requires font file loading, skip */",
    },
    "PDTriangleBasedShadingType": {
        "strategy":  "subclass",
        "subclass":  "PDShadingType4",
        "construction": (
            "PDTriangleBasedShadingType {name} = "
            "new PDShadingType4(new COSDictionary());"
        ),
    },
    "PDStructureNode": {
        "strategy":  "subclass",
        "subclass":  "PDStructureElement",
        "construction": 'PDStructureNode {name} = new PDStructureElement("Div", null);',
    },
    "DecryptionMaterial": {
        "strategy":  "subclass",
        "subclass":  "StandardDecryptionMaterial",
        "construction": 'DecryptionMaterial {name} = new StandardDecryptionMaterial("");',
    },

    # ── Private constructor classes ────────────────────────
    "GroupGraphics": {
        "strategy":  "skip",
        "construction": "/* GroupGraphics {name} — internal graphics wrapper, skip */",
    },
    "ASCII85OutputStream": {
        "strategy":  "skip",
        "construction": "/* ASCII85OutputStream {name} — output stream, skip */",
    },
    "RC4Cipher": {
        "strategy":  "constructor_with_args",
        "construction": "RC4Cipher {name} = new RC4Cipher(new byte[16]);",
    },
    "FontMapperImpl": {
        "strategy":  "constructor",
        "construction": "FontMapperImpl {name} = new FontMapperImpl();",
    },
    "COSOutputStream": {
        "strategy":  "skip",
        "construction": "/* COSOutputStream {name} — output stream, skip */",
    },
    "ASCII85InputStream": {
        "strategy":  "constructor_with_args",
        "construction": (
            "InputStream _is_{name} = "
            "new java.io.ByteArrayInputStream(new byte[0]);\n"
            "ASCII85InputStream {name} = new ASCII85InputStream(_is_{name});"
        ),
    },
    "CCITTFaxDecoderStream": {
        "strategy":  "skip",
        "construction": "/* CCITTFaxDecoderStream {name} — complex decoder, skip */",
    },
    "PredictorOutputStream": {
        "strategy":  "skip",
        "construction": "/* PredictorOutputStream {name} — output stream, skip */",
    },
    "ObjectNumbers": {
        "strategy":  "skip",
        "construction": "/* ObjectNumbers {name} — internal class, skip */",
    },
    "PDPropertyList": {
        "strategy":  "skip",
        "construction": "/* PDPropertyList {name} — skip */",
    },
    "LabelGenerator": {
        "strategy":  "skip",
        "construction": "/* LabelGenerator {name} — internal class, skip */",
    },
}

# ── Types whose constructor param we must NEVER substitute with null ──────────
# FIX 5: When constructor_with_args fallback encounters these types as params,
# it must use the MANUAL_OVERRIDES resolution instead of emitting /* Type */null.
# This prevents the entire null-context NPE cluster.
NEVER_NULL_TYPES = {
    "PDFStreamEngine",
    "PDFGraphicsStreamEngine",
    "PDFTextStripper",
    "PDDocument",
    "PDPage",
}


# ── Scan test resources ───────────────────────────────────
def scan_test_resources(resources_dir):
    result = {}
    if not os.path.isdir(resources_dir):
        print(f"  WARNING: test resources dir not found: {resources_dir}")
        return result
    resources_dir = os.path.normpath(resources_dir)
    for root, dirs, files in os.walk(resources_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, resources_dir).replace(os.sep, '/')
            if ext not in result:
                result[ext] = []
            result[ext].append(rel_path)
    return result


# ── Parse parameter types from a signature string ─────────
def parse_params(signature):
    try:
        inside = signature[signature.index("(") + 1: signature.rindex(")")]
        if not inside.strip():
            return []
        params = []
        for token in inside.split(","):
            token = token.strip()
            if not token:
                continue
            token = token.replace("...", "")
            parts = token.split()
            if len(parts) >= 2:
                ptype = parts[-2]
                pname = parts[-1]
            elif len(parts) == 1:
                ptype = parts[0]
                pname = "arg"
            else:
                continue
            if "<" in ptype:
                ptype = ptype[:ptype.index("<")]
            params.append((ptype, pname))
        return params
    except Exception:
        return []


# ── FIX 4: Walk subclass chain to find a resolvable concrete subclass ─────────
def find_resolvable_subclass(cls_entry, inventory, resource_files, depth=0):
    """
    Given an abstract class entry, walk its concrete_subclasses to find one
    whose constructor we can actually resolve without hitting null for
    NEVER_NULL_TYPES. Returns (subclass_simple_name, construction_string) or None.
    """
    if depth > 3:
        return None

    subclasses = cls_entry.get("concrete_subclasses", [])
    for subclass_name in subclasses:
        # Resolve full name
        sub_entry = inventory.get(subclass_name) or next(
            (v for k, v in inventory.items()
             if v.get("class_name") == subclass_name), None
        )
        if not sub_entry:
            continue
        if sub_entry.get("is_abstract") or sub_entry.get("is_interface"):
            continue

        # Check if it's in MANUAL_OVERRIDES — always resolvable
        if subclass_name in MANUAL_OVERRIDES:
            return subclass_name, None  # caller will use manual override

        pub_ctors = [c for c in sub_entry.get("constructors", [])
                     if c.get("visibility") == "public"]
        if not pub_ctors:
            continue

        simplest = sorted(pub_ctors, key=lambda c: len(c.get("params", [])))[0]
        params = simplest.get("params", [])

        # No-arg constructor — always resolvable
        if not params:
            return subclass_name, f"{subclass_name} _sub = new {subclass_name}();"

        # Check if all params are resolvable (not hitting unknown types outside overrides)
        all_ok = True
        for p in params:
            if p in NEVER_NULL_TYPES and p not in MANUAL_OVERRIDES:
                all_ok = False
                break
            if p not in PRIMITIVES and p not in SIMPLE_TYPES and p not in MANUAL_OVERRIDES:
                # Try inventory lookup
                found = inventory.get(p) or next(
                    (v for k, v in inventory.items()
                     if v.get("class_name") == p), None
                )
                if not found:
                    all_ok = False
                    break
        if all_ok:
            return subclass_name, None  # caller will build full construction

    return None


# ── Resolve a single type to a construction strategy ──────
def resolve_type(type_name, param_name, inventory, resource_files, depth=0):
    if depth > 3:
        return {
            "type":         type_name,
            "name":         param_name,
            "strategy":     "unknown",
            "construction": f"/* TODO: construct {type_name} */",
            "reason":       "max recursion depth reached",
        }

    # 0. Manual override — checked first
    if type_name in MANUAL_OVERRIDES:
        override = MANUAL_OVERRIDES[type_name].copy()
        override["type"]         = type_name
        override["name"]         = param_name
        override["construction"] = override["construction"].replace("{name}", param_name)
        return override

    # 1. Primitive
    if type_name in PRIMITIVES:
        return {
            "type":         type_name,
            "name":         param_name,
            "strategy":     "primitive",
            "construction": f"{type_name} {param_name} = {PRIMITIVES[type_name]};",
        }

    # 2. Simple known Java type
    if type_name in SIMPLE_TYPES:
        return {
            "type":         type_name,
            "name":         param_name,
            "strategy":     "simple",
            "construction": f"{type_name} {param_name} = {SIMPLE_TYPES[type_name]};",
        }

    # 3. Skip output types
    if type_name in SKIP_TYPES:
        return {
            "type":         type_name,
            "name":         param_name,
            "strategy":     "skip",
            "construction": f"/* {type_name} {param_name} — output type, skip */",
            "reason":       "output stream or writer — not needed for input-side testing",
        }

    # 4. File-related → use test resource
    if type_name in FILE_TYPES:
        chosen_file = None
        for ext in [".pdf", ".fdf", ".xfdf", ".xml", ".txt"]:
            if ext in resource_files and resource_files[ext]:
                chosen_file = resource_files[ext][0]
                break
        if not chosen_file and resource_files:
            for ext, files in resource_files.items():
                if files:
                    chosen_file = files[0]
                    break

        if chosen_file:
            if type_name == "File":
                construction = (
                    f'File {param_name} = new File(getClass().getClassLoader()'
                    f'.getResource("{chosen_file}").toURI());'
                )
            elif type_name in ("InputStream", "FileInputStream", "BufferedInputStream"):
                construction = (
                    f'InputStream {param_name} = getClass().getClassLoader()'
                    f'.getResourceAsStream("{chosen_file}");'
                )
            elif type_name == "RandomAccessReadBufferedFile":
                construction = (
                    f'File _file_{param_name} = new File(getClass().getClassLoader()'
                    f'.getResource("{chosen_file}").toURI());\n'
                    f'RandomAccessReadBufferedFile {param_name} = '
                    f'new RandomAccessReadBufferedFile(_file_{param_name});'
                )
            elif type_name in ("RandomAccessRead", "RandomAccessReadBuffer"):
                # FIX 6: RandomAccessReadBuffer now gets a real construction recipe
                construction = (
                    f'InputStream _is_{param_name} = getClass().getClassLoader()'
                    f'.getResourceAsStream("{chosen_file}");\n'
                    f'RandomAccessReadBuffer {param_name} = '
                    f'new RandomAccessReadBuffer(_is_{param_name});'
                )
            else:
                construction = (
                    f'File {param_name} = new File(getClass().getClassLoader()'
                    f'.getResource("{chosen_file}").toURI());'
                )
            return {
                "type":          type_name,
                "name":          param_name,
                "strategy":      "resource",
                "resource_file": chosen_file,
                "construction":  construction,
            }
        else:
            return {
                "type":         type_name,
                "name":         param_name,
                "strategy":     "tempfile",
                "construction": (
                    f'File {param_name} = File.createTempFile("test", ".tmp");\n'
                    f'{param_name}.deleteOnExit();'
                ),
                "reason": "no resource files found, using temp file",
            }

    # 5. Array type
    if type_name.endswith("[]"):
        base = type_name[:-2]
        return {
            "type":         type_name,
            "name":         param_name,
            "strategy":     "array",
            "construction": f"{type_name} {param_name} = new {base}[0];",
        }

    # 6. Common collection types
    if type_name in ("List", "ArrayList"):
        return {
            "type":         type_name,
            "name":         param_name,
            "strategy":     "collection",
            "construction": f"List<COSBase> {param_name} = new ArrayList<>();",
        }
    if type_name in ("Map", "HashMap"):
        return {
            "type":         type_name,
            "name":         param_name,
            "strategy":     "collection",
            "construction": f"Map {param_name} = new HashMap<>();",
        }

    # 7. Look up in class inventory
    cls = inventory.get(type_name)
    if not cls:
        for key, val in inventory.items():
            if key.endswith("." + type_name) or val.get("class_name") == type_name:
                cls = val
                break

    if cls:
        is_abstract     = cls.get("is_abstract", False)
        is_interface    = cls.get("is_interface", False)
        is_enum         = cls.get("is_enum", False)
        constructors    = cls.get("constructors", [])
        factory_methods = cls.get("factory_methods", [])
        subclasses      = cls.get("concrete_subclasses", [])

        # 7a. Enum
        if is_enum:
            return {
                "type":         type_name,
                "name":         param_name,
                "strategy":     "enum",
                "construction": f"{type_name} {param_name} = {type_name}.values()[0];",
            }

        # 7b. Abstract or interface → FIX 4: walk subclass chain properly
        if is_abstract or is_interface:
            if subclasses:
                result = find_resolvable_subclass(cls, inventory, resource_files)
                if result:
                    subclass_name, hint_construction = result
                    # If subclass is in MANUAL_OVERRIDES, use that
                    if subclass_name in MANUAL_OVERRIDES:
                        override = MANUAL_OVERRIDES[subclass_name].copy()
                        override["type"]         = type_name
                        override["name"]         = param_name
                        override["construction"] = override["construction"].replace(
                            "{name}", param_name
                        )
                        # Re-type the declaration to parent type
                        override["construction"] = override["construction"].replace(
                            f"{subclass_name} {param_name}",
                            f"{type_name} {param_name}"
                        )
                        return override
                    # Otherwise build simple subclass construction
                    sub_entry = inventory.get(subclass_name) or next(
                        (v for k, v in inventory.items()
                         if v.get("class_name") == subclass_name), None
                    )
                    if sub_entry:
                        pub_ctors = [c for c in sub_entry.get("constructors", [])
                                     if c.get("visibility") == "public"]
                        no_arg = [c for c in pub_ctors if not c.get("params")]
                        if no_arg:
                            return {
                                "type":         type_name,
                                "name":         param_name,
                                "strategy":     "subclass",
                                "subclass":     subclass_name,
                                "construction": (
                                    f"{type_name} {param_name} = new {subclass_name}();"
                                ),
                            }

            # No resolvable subclass found
            return {
                "type":         type_name,
                "name":         param_name,
                "strategy":     "unresolvable_abstract",
                "construction": (
                    f"/* {type_name} {param_name} "
                    f"— abstract/interface, no concrete subclass found */"
                ),
                "reason": "abstract or interface with no concrete subclass in inventory",
            }

        # 7c. Factory method
        if factory_methods:
            fm        = factory_methods[0]
            fm_params = fm.get("params", [])
            if not fm_params:
                return {
                    "type":           type_name,
                    "name":           param_name,
                    "strategy":       "factory",
                    "factory_method": fm["name"],
                    "construction":   (
                        f"{type_name} {param_name} = {type_name}.{fm['name']}();"
                    ),
                }
            elif len(fm_params) == 1 and fm_params[0] in ("String", "str"):
                return {
                    "type":           type_name,
                    "name":           param_name,
                    "strategy":       "factory",
                    "factory_method": fm["name"],
                    "construction":   (
                        f'{type_name} {param_name} = '
                        f'{type_name}.{fm["name"]}("test");'
                    ),
                }

        # 7d. Public no-arg constructor
        public_ctors = [c for c in constructors if c.get("visibility") == "public"]
        no_arg       = [c for c in public_ctors if not c.get("params")]
        if no_arg:
            return {
                "type":         type_name,
                "name":         param_name,
                "strategy":     "constructor",
                "construction": f"{type_name} {param_name} = new {type_name}();",
            }

        # 7e. Simplest public constructor with args
        # FIX 5: Never emit null for NEVER_NULL_TYPES — use their MANUAL_OVERRIDES
        if public_ctors:
            simplest = sorted(
                public_ctors, key=lambda c: len(c.get("params", []))
            )[0]
            param_literals = []
            preamble_lines = []
            for idx, p in enumerate(simplest.get("params", [])):
                var_name = f"_p{idx}_{param_name}"
                if p in PRIMITIVES:
                    param_literals.append(PRIMITIVES[p])
                elif p in SIMPLE_TYPES:
                    param_literals.append(SIMPLE_TYPES[p])
                elif p == "String":
                    param_literals.append('"test"')
                elif p in NEVER_NULL_TYPES or p in MANUAL_OVERRIDES:
                    # FIX 5: resolve properly instead of null
                    resolved = resolve_type(p, var_name, inventory, resource_files, depth + 1)
                    preamble_lines.append(resolved["construction"])
                    param_literals.append(var_name)
                else:
                    param_literals.append(f"/* {p} */null")
            args = ", ".join(param_literals)
            construction = ""
            if preamble_lines:
                construction = "\n".join(preamble_lines) + "\n"
            construction += f"{type_name} {param_name} = new {type_name}({args});"
            return {
                "type":         type_name,
                "name":         param_name,
                "strategy":     "constructor_with_args",
                "construction": construction,
            }

        # 7f. Abstract/interface — no resolvable subclass
        if is_abstract or is_interface:
            return {
                "type":         type_name,
                "name":         param_name,
                "strategy":     "unresolvable_abstract",
                "construction": (
                    f"/* {type_name} {param_name} "
                    f"— abstract/interface, no concrete subclass found */"
                ),
                "reason": "abstract or interface with no concrete subclass in inventory",
            }

        # 7g. Only private constructors
        return {
            "type":         type_name,
            "name":         param_name,
            "strategy":     "private_constructor",
            "construction": (
                f"/* {type_name} {param_name} "
                f"— constructor is private, needs factory method */"
            ),
            "reason": "all constructors are private",
        }

    # 8. Not found in inventory at all
    return {
        "type":         type_name,
        "name":         param_name,
        "strategy":     "unknown",
        "construction": (
            f"/* TODO: {type_name} {param_name} "
            f"— not found in class_inventory.json */"
        ),
        "reason": f"class '{type_name}' not found in class_inventory.json",
    }


# ── Resolve receiver for instance methods ─────────────────
def resolve_receiver(method, inventory, resource_files):
    sig          = method.get("signature", "")
    before_paren = sig.split("(")[0] if "(" in sig else sig
    if "static" in before_paren.lower().split():
        return None

    class_name = method.get("class_name", "")
    if not class_name:
        return None

    return resolve_type(class_name, "instance", inventory, resource_files, depth=0)


# ── Main ──────────────────────────────────────────────────
def main():
    print(f"Loading metadata from {METADATA_FILE}...")
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    print(f"  {len(metadata)} method entries")

    print(f"Loading class inventory from {CLASS_INVENTORY}...")
    with open(CLASS_INVENTORY, "r", encoding="utf-8") as f:
        inventory = json.load(f)
    print(f"  {len(inventory)} classes in inventory")

    print(f"Scanning test resources from {TEST_RESOURCES_DIR}...")
    resource_files = scan_test_resources(TEST_RESOURCES_DIR)
    for ext, files in resource_files.items():
        print(f"  {ext}: {len(files)} files")

    if UNCOVERED_ONLY:
        target_methods = [
            m for m in metadata
            if not m.get("has_developer_tests", False)
        ]
        print(f"\nUncovered methods (no developer tests): {len(target_methods)}")
    else:
        target_methods = metadata
        print(f"\nProcessing all {len(target_methods)} methods")

    results = {}
    stats = {
        "total":               0,
        "fully_resolved":      0,
        "partially_resolved":  0,
        "has_unknown":         0,
        "has_unresolvable":    0,
        "static_methods":      0,
        "instance_methods":    0,
    }

    for i, method in enumerate(target_methods):
        if i > 0 and i % 100 == 0:
            print(f"  Progress: {i}/{len(target_methods)}")

        full_name = method.get("full_name", "")
        signature = method.get("signature", "")
        key       = f"{full_name}|{signature}"

        stats["total"] += 1

        params          = parse_params(signature)
        resolved_params = []
        for ptype, pname in params:
            resolved = resolve_type(ptype, pname, inventory, resource_files, depth=0)
            resolved_params.append(resolved)

        receiver = resolve_receiver(method, inventory, resource_files)
        if receiver is None:
            stats["static_methods"] += 1
        else:
            stats["instance_methods"] += 1

        strategies = [p["strategy"] for p in resolved_params]
        if receiver:
            strategies.append(receiver["strategy"])

        has_unknown      = any(s == "unknown" for s in strategies)
        has_unresolvable = any(
            s in ("unresolvable_abstract", "private_constructor")
            for s in strategies
        )

        if has_unknown:
            stats["has_unknown"] += 1
        elif has_unresolvable:
            stats["has_unresolvable"] += 1
        elif any(s == "skip" for s in strategies):
            stats["partially_resolved"] += 1
        else:
            stats["fully_resolved"] += 1

        quality = (
            "unknown"      if has_unknown      else
            "unresolvable" if has_unresolvable else
            "partial"      if any(s == "skip" for s in strategies) else
            "full"
        )

        results[key] = {
            "full_name":          full_name,
            "signature":          signature,
            "class_name":         method.get("class_name", ""),
            "method_name":        method.get("method_name", ""),
            "is_static":          receiver is None,
            "receiver":           receiver,
            "params":             resolved_params,
            "resolution_quality": quality,
        }

    print(f"\nSaving to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n========== SUMMARY ==========")
    print(f"Methods processed        : {stats['total']}")
    print(f"Fully resolved           : {stats['fully_resolved']}")
    print(f"Partially resolved (skip): {stats['partially_resolved']}")
    print(f"Has unknown types        : {stats['has_unknown']}")
    print(f"Has unresolvable types   : {stats['has_unresolvable']}")
    print(f"Static methods           : {stats['static_methods']}")
    print(f"Instance methods         : {stats['instance_methods']}")
    print(f"Output saved to          : {OUTPUT_FILE}")
    print("==============================")


if __name__ == "__main__":
    main()