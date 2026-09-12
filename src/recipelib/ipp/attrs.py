"""The printer's attribute set, modelled on CUPS ippeveprinter and the
AirPrint/IPP Everywhere must-haves gathered from PAPPL and airprinthq."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import codec as C
from .codec import Attr, Collection, Range, Resolution

FORMATS = ["application/pdf", "image/urf", "image/pwg-raster", "image/jpeg", "image/png",
           "application/octet-stream"]
URF = ["W8", "SRGB24", "CP1", "IS1", "MT1-2-3", "OB10", "PQ3-4-5", "RS300", "V1.4", "DM1"]
OPS = [0x0002, 0x0004, 0x0005, 0x0006, 0x0008, 0x0009, 0x000A, 0x000B, 0x0015, 0x003B, 0x003C, 0x0039]

MEDIA = {
    # keyword: (x hundredths mm, y hundredths mm)
    "na_letter_8.5x11in": (21590, 27940),
    "iso_a4_210x297mm": (21000, 29700),
    "na_legal_8.5x14in": (21590, 35560),
    "iso_a5_148x210mm": (14800, 21000),
    "na_index-4x6_4x6in": (10160, 15240),
}
MARGIN = 423   # ~4.2 mm in hundredths of mm


def media_col(keyword: str, media_type: str = "stationery", source: str = "main") -> Collection:
    x, y = MEDIA[keyword]
    size = Collection([Attr("x-dimension", C.INTEGER, [x]), Attr("y-dimension", C.INTEGER, [y])])
    return Collection([
        Attr("media-size", C.BEG_COLLECTION, [size]),
        Attr("media-size-name", C.KEYWORD, [keyword]),
        Attr("media-type", C.KEYWORD, [media_type]),
        Attr("media-source", C.KEYWORD, [source]),
        Attr("media-bottom-margin", C.INTEGER, [MARGIN]),
        Attr("media-left-margin", C.INTEGER, [MARGIN]),
        Attr("media-right-margin", C.INTEGER, [MARGIN]),
        Attr("media-top-margin", C.INTEGER, [MARGIN]),
    ])


def media_size_col(keyword: str) -> Collection:
    x, y = MEDIA[keyword]
    return Collection([Attr("x-dimension", C.INTEGER, [x]), Attr("y-dimension", C.INTEGER, [y])])


class PrinterInfo:
    """Everything that varies per install: name, URIs, uuid, paper, start time."""

    def __init__(self, name: str, location: str, uuid: str, uris: list[str], web_url: str,
                 icon_urls: list[str], paper: str = "letter"):
        self.name = name
        self.location = location
        self.uuid = uuid
        self.uris = uris
        self.web_url = web_url
        self.icon_urls = icon_urls
        self.default_media = "iso_a4_210x297mm" if paper.lower() == "a4" else "na_letter_8.5x11in"
        self.started = time.time()
        self.config_change = int(time.time())

    @property
    def dns_sd_name(self) -> str:
        return self.name


# attribute name -> group keyword used by requested-attributes filtering
DESCRIPTION = "printer-description"
TEMPLATE = "job-template"
MEDIA_DB = "media-col-database"


def printer_attributes(info: PrinterInfo, queued_jobs: int, requested: list[str] | None) -> list[Attr]:
    now = datetime.now(timezone.utc)
    uptime = int(time.time() - info.started) + 1
    media_keys = list(MEDIA.keys())
    ready = [info.default_media, "iso_a4_210x297mm" if info.default_media != "iso_a4_210x297mm" else "na_letter_8.5x11in"]

    desc: list[Attr] = [
        Attr("printer-uri-supported", C.URI, list(info.uris)),
        Attr("uri-security-supported", C.KEYWORD, ["none"] * len(info.uris)),
        Attr("uri-authentication-supported", C.KEYWORD, ["none"] * len(info.uris)),
        Attr("printer-name", C.NAME, [info.name]),
        Attr("printer-info", C.TEXT, [info.name]),
        Attr("printer-location", C.TEXT, [info.location]),
        Attr("printer-make-and-model", C.TEXT, ["Recipe Library Virtual Printer"]),
        Attr("printer-more-info", C.URI, [info.web_url]),
        Attr("printer-supply-info-uri", C.URI, [info.web_url]),
        Attr("printer-uuid", C.URI, [f"urn:uuid:{info.uuid}"]),
        Attr("printer-dns-sd-name", C.NAME, [info.dns_sd_name]),
        Attr("printer-device-id", C.TEXT, ["MFG:RecipeLib;MDL:Recipe Library;CMD:PDF,URF,PWGRaster;CLS:PRINTER;"]),
        Attr("printer-icons", C.URI, list(info.icon_urls)),
        Attr("printer-geo-location", C.UNKNOWN, []),
        Attr("printer-organization", C.TEXT, ["Home"]),
        Attr("printer-organizational-unit", C.TEXT, ["Kitchen"]),
        Attr("printer-state", C.ENUM, [3]),
        Attr("printer-state-reasons", C.KEYWORD, ["none"]),
        Attr("printer-state-message", C.TEXT, ["Ready to save recipes"]),
        Attr("printer-is-accepting-jobs", C.BOOLEAN, [True]),
        Attr("queued-job-count", C.INTEGER, [queued_jobs]),
        Attr("printer-up-time", C.INTEGER, [uptime]),
        Attr("printer-current-time", C.DATE_TIME, [now]),
        Attr("printer-config-change-time", C.INTEGER, [1]),
        Attr("printer-config-change-date-time", C.DATE_TIME, [datetime.fromtimestamp(info.config_change, timezone.utc)]),
        Attr("printer-state-change-time", C.INTEGER, [1]),
        Attr("printer-state-change-date-time", C.DATE_TIME, [datetime.fromtimestamp(info.started, timezone.utc)]),
        Attr("ipp-versions-supported", C.KEYWORD, ["1.0", "1.1", "2.0"]),
        Attr("ipp-features-supported", C.KEYWORD, ["airprint-1.4", "airprint-1.5", "airprint-1.6", "airprint-1.7", "airprint-1.8", "ipp-everywhere"]),
        Attr("operations-supported", C.ENUM, list(OPS)),
        Attr("charset-configured", C.CHARSET, ["utf-8"]),
        Attr("charset-supported", C.CHARSET, ["utf-8"]),
        Attr("natural-language-configured", C.LANGUAGE, ["en"]),
        Attr("generated-natural-language-supported", C.LANGUAGE, ["en"]),
        Attr("document-format-default", C.MIME, ["application/pdf"]),
        Attr("document-format-supported", C.MIME, list(FORMATS)),
        Attr("document-format-preferred", C.MIME, ["application/pdf"]),
        Attr("compression-supported", C.KEYWORD, ["none"]),
        Attr("pdl-override-supported", C.KEYWORD, ["attempted"]),
        Attr("multiple-document-jobs-supported", C.BOOLEAN, [True]),
        Attr("multiple-operation-time-out", C.INTEGER, [120]),
        Attr("multiple-operation-time-out-action", C.KEYWORD, ["process-job"]),
        Attr("job-creation-attributes-supported", C.KEYWORD, [
            "copies", "document-format", "job-name", "media", "media-col", "orientation-requested",
            "print-color-mode", "print-quality", "printer-resolution", "sides", "ipp-attribute-fidelity",
            "print-scaling", "job-hold-until", "requesting-user-name"]),
        Attr("job-ids-supported", C.BOOLEAN, [True]),
        Attr("which-jobs-supported", C.KEYWORD, ["completed", "not-completed", "aborted", "canceled", "pending", "processing"]),
        Attr("identify-actions-default", C.KEYWORD, ["display"]),
        Attr("identify-actions-supported", C.KEYWORD, ["display"]),
        Attr("color-supported", C.BOOLEAN, [True]),
        Attr("pages-per-minute", C.INTEGER, [30]),
        Attr("pages-per-minute-color", C.INTEGER, [30]),
        Attr("printer-kind", C.KEYWORD, ["document", "envelope", "photo"]),
        Attr("printer-resolution-default", C.RESOLUTION, [Resolution(300, 300)]),
        Attr("printer-resolution-supported", C.RESOLUTION, [Resolution(300, 300)]),
        Attr("pwg-raster-document-resolution-supported", C.RESOLUTION, [Resolution(300, 300)]),
        Attr("pwg-raster-document-sheet-back", C.KEYWORD, ["normal"]),
        Attr("pwg-raster-document-type-supported", C.KEYWORD, ["black_1", "sgray_8", "srgb_8"]),
        Attr("urf-supported", C.KEYWORD, list(URF)),
        Attr("landscape-orientation-requested-preferred", C.ENUM, [5]),
        Attr("media-col-ready", C.BEG_COLLECTION, [media_col(k) for k in ready]),
        Attr("media-ready", C.KEYWORD, list(ready)),
        Attr("job-password-supported", C.INTEGER, [0]),
        Attr("printer-input-tray", C.OCTET_STRING, [b"type=sheetFeedAutoRemovableTray;mediafeed=0;mediaxfeed=0;maxcapacity=250;level=250;status=0;name=main;"]),
        Attr("printer-output-tray", C.OCTET_STRING, [b"type=unRemovableBin;maxcapacity=100;remaining=100;status=0;name=face-down;stackingorder=lastToFirst;pagedelivery=faceDown;"]),
        Attr("marker-names", C.NAME, ["Cloud ink"]),
        Attr("marker-types", C.KEYWORD, ["ink-cartridge"]),
        Attr("marker-colors", C.NAME, ["#000000"]),
        Attr("marker-levels", C.INTEGER, [100]),
        Attr("marker-low-levels", C.INTEGER, [10]),
        Attr("marker-high-levels", C.INTEGER, [100]),
    ]
    template: list[Attr] = [
        Attr("copies-default", C.INTEGER, [1]),
        Attr("copies-supported", C.RANGE, [Range(1, 1)]),
        Attr("finishings-default", C.ENUM, [3]),
        Attr("finishings-supported", C.ENUM, [3]),
        Attr("media-default", C.KEYWORD, [info.default_media]),
        Attr("media-supported", C.KEYWORD, media_keys),
        Attr("media-col-default", C.BEG_COLLECTION, [media_col(info.default_media)]),
        Attr("media-col-supported", C.KEYWORD, ["media-size", "media-size-name", "media-type", "media-source",
                                                "media-bottom-margin", "media-left-margin", "media-right-margin", "media-top-margin"]),
        Attr("media-size-supported", C.BEG_COLLECTION, [media_size_col(k) for k in media_keys]),
        Attr("media-type-supported", C.KEYWORD, ["stationery", "photographic"]),
        Attr("media-source-supported", C.KEYWORD, ["main"]),
        Attr("media-bottom-margin-supported", C.INTEGER, [MARGIN]),
        Attr("media-left-margin-supported", C.INTEGER, [MARGIN]),
        Attr("media-right-margin-supported", C.INTEGER, [MARGIN]),
        Attr("media-top-margin-supported", C.INTEGER, [MARGIN]),
        Attr("orientation-requested-default", C.NO_VALUE, []),
        Attr("orientation-requested-supported", C.ENUM, [3, 4, 5, 6]),
        Attr("output-bin-default", C.KEYWORD, ["face-down"]),
        Attr("output-bin-supported", C.KEYWORD, ["face-down"]),
        Attr("print-color-mode-default", C.KEYWORD, ["color"]),
        Attr("print-color-mode-supported", C.KEYWORD, ["auto", "color", "monochrome"]),
        Attr("print-content-optimize-default", C.KEYWORD, ["auto"]),
        Attr("print-content-optimize-supported", C.KEYWORD, ["auto"]),
        Attr("print-quality-default", C.ENUM, [4]),
        Attr("print-quality-supported", C.ENUM, [3, 4, 5]),
        Attr("print-scaling-default", C.KEYWORD, ["auto"]),
        Attr("print-scaling-supported", C.KEYWORD, ["auto", "auto-fit", "fill", "fit", "none"]),
        Attr("sides-default", C.KEYWORD, ["one-sided"]),
        Attr("sides-supported", C.KEYWORD, ["one-sided"]),
        Attr("job-hold-until-default", C.KEYWORD, ["no-hold"]),
        Attr("job-hold-until-supported", C.KEYWORD, ["no-hold"]),
        Attr("job-priority-default", C.INTEGER, [50]),
        Attr("job-priority-supported", C.INTEGER, [1]),
        Attr("job-sheets-default", C.KEYWORD, ["none"]),
        Attr("job-sheets-supported", C.KEYWORD, ["none"]),
        Attr("number-up-default", C.INTEGER, [1]),
        Attr("number-up-supported", C.INTEGER, [1]),
        Attr("page-ranges-supported", C.BOOLEAN, [False]),
    ]
    media_db = [Attr("media-col-database", C.BEG_COLLECTION, [media_col(k) for k in media_keys])]

    want = set(requested or ["all"])
    out: list[Attr] = []
    if "all" in want or DESCRIPTION in want:
        out += desc
    if "all" in want or TEMPLATE in want:
        out += template
    if MEDIA_DB in want:
        out += media_db
    if "all" in want or DESCRIPTION in want or TEMPLATE in want:
        if MEDIA_DB not in want and "all" in want:
            pass
    # explicitly named attributes
    named = {a.name: a for a in desc + template + media_db}
    for name in want:
        if name in named and named[name] not in out:
            out.append(named[name])
    return out
