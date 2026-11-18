#!/usr/bin/env python3
"""Actualiza la sección de noticias de index.html.

Lee los últimos titulares de Ignacio Online y Blog del Contador (RSS, API de
WordPress o portada) y reescribe solo el bloque entre
<!-- NOTICIAS:INICIO ... --> y <!-- NOTICIAS:FIN -->, más la fecha de
"Última actualización". Si de un sitio no se puede leer nada, conserva los
titulares que ese sitio ya tenía en la página.
"""
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ARCHIVO = "index.html"
POR_SITIO = 4
MAXIMO = 8
TZ_BA = timezone(timedelta(hours=-3))
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SITIOS = {
    "Ignacio Online": {
        "portada": "https://www.ignacioonline.com.ar/",
        "feeds": ["https://www.ignacioonline.com.ar/feed/"],
        "wp_api": "https://www.ignacioonline.com.ar/wp-json/wp/v2/posts?per_page=10&_fields=link,date,title",
    },
    "Blog del Contador": {
        "portada": "https://blogdelcontador.com.ar/",
        "feeds": [
            "https://blogdelcontador.com.ar/rss",
            "https://blogdelcontador.com.ar/feed",
            "https://blogdelcontador.com.ar/feed/",
            "https://blogdelcontador.com.ar/rss.xml",
        ],
        "wp_api": None,
        "patron_nota": r'href="((?:https?://(?:www\.)?blogdelcontador\.com\.ar)?/news-\d+-[^"#?]+)"',
    },
}


def log(*a):
    print(*a, file=sys.stderr)


def bajar(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def limpiar(texto):
    texto = re.sub(r"<[^>]+>", "", texto or "")
    return re.sub(r"\s+", " ", html.unescape(texto)).strip()


def fecha_de(texto):
    if not texto:
        return None
    texto = texto.strip()
    try:
        return parsedate_to_datetime(texto).astimezone(TZ_BA)
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=TZ_BA)).astimezone(TZ_BA)
    except Exception:
        pass
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", texto)
    if m:
        return datetime(int(m[3]), int(m[2]), int(m[1]), tzinfo=TZ_BA)
    return None


def desde_feed(url):
    raiz = ET.fromstring(bajar(url).lstrip("\ufeff").strip())
    notas = []
    for it in raiz.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        hijos = {c.tag.split("}")[-1]: c for c in it}
        titulo = limpiar(hijos["title"].text if "title" in hijos else "")
        link = ""
        if "link" in hijos:
            link = (hijos["link"].text or hijos["link"].get("href") or "").strip()
        fecha = None
        for k in ("pubDate", "published", "updated", "date"):
            if k in hijos:
                fecha = fecha_de(hijos[k].text)
                if fecha:
                    break
        if titulo and link and fecha:
            notas.append((fecha, titulo, link))
    return notas


def desde_wp_api(url):
    notas = []
    for p in json.loads(bajar(url)):
        fecha = fecha_de(p.get("date"))
        titulo = limpiar((p.get("title") or {}).get("rendered", ""))
        if fecha and titulo and p.get("link"):
            notas.append((fecha, titulo, p["link"]))
    return notas


def desde_portada(cfg):
    """Toma enlaces de notas de la portada y abre cada nota para leer título y fecha."""
    portada = bajar(cfg["portada"])
    vistos, notas = [], []
    for href in re.findall(cfg["patron_nota"], portada):
        url = href if href.startswith("http") else cfg["portada"].rstrip("/") + href
        url = url.replace("/amp", "")
        if url not in vistos:
            vistos.append(url)
    for url in vistos[:40]:
        try:
            pag = bajar(url)
        except Exception as e:
            log("  no se pudo abrir", url, e)
            continue
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', pag) or \
            re.search(r"<h1[^>]*>(.*?)</h1>", pag, re.S)
        f = re.search(r"Fecha de publicaci[oó]n:\s*(\d{2}/\d{2}/\d{4})", pag) or \
            re.search(r'"datePublished"\s*:\s*"([^"]+)"', pag)
        if m and f:
            titulo = re.sub(r"\s*/\s*Blog del Contador\s*$", "", limpiar(m.group(1)))
            fecha = fecha_de(f.group(1))
            if titulo and fecha:
                notas.append((fecha, titulo, url))
    return notas


def notas_de(nombre, cfg):
    intentos = [(desde_feed, u) for u in cfg["feeds"]]
    if cfg.get("wp_api"):
        intentos.append((desde_wp_api, cfg["wp_api"]))
    if cfg.get("patron_nota"):
        intentos.append((desde_portada, cfg))
    for funcion, arg in intentos:
        try:
            notas = funcion(arg)
        except Exception as e:
            log(f"{nombre}: {funcion.__name__} falló ({e})")
            continue
        if notas:
            notas.sort(key=lambda n: n[0], reverse=True)
            log(f"{nombre}: {len(notas)} notas con {funcion.__name__}")
            return notas[:POR_SITIO]
        log(f"{nombre}: {funcion.__name__} no devolvió notas")
    return []


PATRON_ITEM = re.compile(
    r'<a href="([^"]*)"><span class="src">([^<]*)</span>'
    r'<span class="ttl">(.*?)</span><span class="dt">(\d{2}/\d{2}/\d{4})</span></a>')


def main():
    texto = open(ARCHIVO, encoding="utf-8").read()
    bloque = re.search(r"(<!-- NOTICIAS:INICIO[^>]*-->\n)(.*?)(\n[ \t]*<!-- NOTICIAS:FIN -->)", texto, re.S)
    if not bloque:
        sys.exit("No encontré los marcadores NOTICIAS:INICIO / NOTICIAS:FIN")

    existentes = {}
    for href, src, ttl, dt in PATRON_ITEM.findall(bloque.group(2)):
        existentes.setdefault(src, []).append((fecha_de(dt), html.unescape(ttl), href))

    todas = []
    for nombre, cfg in SITIOS.items():
        nuevas = notas_de(nombre, cfg)
        if nuevas:
            todas += [(f, nombre, t, l) for f, t, l in nuevas]
        else:
            log(f"{nombre}: sin notas nuevas, conservo las que ya estaban")
            todas += [(f, nombre, t, l) for f, t, l in existentes.get(nombre, [])]

    todas.sort(key=lambda n: n[0], reverse=True)
    todas = todas[:MAXIMO]
    if not todas:
        sys.exit("No hay titulares para mostrar; no toco el archivo")

    filas = "\n".join(
        f'        <a href="{html.escape(l, quote=True)}"><span class="src">{n}</span>'
        f'<span class="ttl">{html.escape(t, quote=True)}</span>'
        f'<span class="dt">{f.strftime("%d/%m/%Y")}</span></a>'
        for f, n, t, l in todas)
    nuevo = '      <div class="news">\n' + filas + "\n      </div>"
    texto = texto[:bloque.start(2)] + nuevo + texto[bloque.end(2):]

    hoy = datetime.now(TZ_BA).strftime("%d/%m/%Y")
    texto = re.sub(r"Última actualización: \d{2}/\d{2}/\d{4}\.", f"Última actualización: {hoy}.", texto, count=1)
    open(ARCHIVO, "w", encoding="utf-8").write(texto)

    for f, n, t, l in todas:
        print(f"{f:%d/%m/%Y}  {n}: {t}")


if __name__ == "__main__":
    main()
