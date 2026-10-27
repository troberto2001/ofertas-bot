"""Local ES -> pt-PT glossary for '[product] [brand/model]' titles.

Only a recognised product prefix and its qualifiers are translated. The first
unknown segment and everything after it are left unchanged. This deliberately
does not attempt general sentence translation or guess unknown brand names.
"""
from __future__ import annotations

import re

PRODUCTS = {
    "zapatillas de deporte": "sapatilhas",
    "zapatillas deportivas": "sapatilhas desportivas",
    "zapatillas": "sapatilhas", "zapatilla": "sapatilha",
    "zapatos": "sapatos", "zapato": "sapato",
    "chanclas": "chinelos", "chancla": "chinelo",
    "sandalias": "sandálias", "sandalia": "sandália",
    "botas": "botas", "botines": "botins", "mocasines": "mocassins",
    "camisetas": "t-shirts", "camiseta": "t-shirt",
    "camisas": "camisas", "camisa": "camisa",
    "sudaderas": "sweatshirts", "sudadera": "sweatshirt",
    "jerséis": "camisolas", "jerseis": "camisolas", "jersey": "camisola",
    "chaquetas": "casacos", "chaqueta": "casaco",
    "abrigos": "casacos", "abrigo": "casaco",
    "cazadoras": "blusões", "cazadora": "blusão",
    "chalecos": "coletes", "chaleco": "colete",
    "chubasqueros": "impermeáveis", "chubasquero": "impermeável",
    "pantalones cortos": "calções", "pantalón corto": "calções",
    "pantalon corto": "calções", "pantalones vaqueros": "calças de ganga",
    "pantalones": "calças", "pantalón": "calças", "pantalon": "calças",
    "vaqueros": "calças de ganga", "chándal": "fato de treino",
    "chandal": "fato de treino", "chándales": "fatos de treino",
    "chandales": "fatos de treino", "vestidos": "vestidos", "vestido": "vestido",
    "faldas": "saias", "falda": "saia", "bañadores": "fatos de banho",
    "bañador": "fato de banho", "banador": "fato de banho",
    "bikinis": "biquínis", "bikini": "biquíni",
    "ropa interior": "roupa interior", "calzoncillos": "boxers",
    "bragas": "cuecas", "sujetadores": "soutiens", "sujetador": "soutien",
    "calcetines": "meias", "calcetín": "meia", "calcetin": "meia",
    "pack de calcetines": "pack de meias", "pack de camisetas": "pack de t-shirts",
    "bolsos": "malas", "bolso": "mala", "mochilas": "mochilas", "mochila": "mochila",
    "riñoneras": "bolsas de cintura", "riñonera": "bolsa de cintura",
    "rinonera": "bolsa de cintura", "bandoleras": "malas a tiracolo",
    "bandolera": "mala a tiracolo", "carteras": "carteiras", "cartera": "carteira",
    "monederos": "porta-moedas", "monedero": "porta-moedas",
    "gorras": "bonés", "gorra": "boné", "gorros": "gorros", "gorro": "gorro",
    "sombreros": "chapéus", "sombrero": "chapéu",
    "bufandas": "cachecóis", "bufanda": "cachecol",
    "guantes": "luvas", "cinturones": "cintos", "cinturón": "cinto",
    "cinturon": "cinto", "gafas de sol": "óculos de sol", "gafas": "óculos",
    "relojes": "relógios", "reloj": "relógio", "pulseras": "pulseiras",
    "pulsera": "pulseira", "collares": "colares", "collar": "colar",
    "pendientes": "brincos", "anillos": "anéis", "anillo": "anel",
    "auriculares": "auscultadores", "altavoces": "colunas", "altavoz": "coluna",
    "teléfonos": "telefones", "teléfono": "telefone",
    "cargadores": "carregadores", "cargador": "carregador",
    "fundas": "capas", "funda": "capa", "teclados": "teclados", "teclado": "teclado",
    "ratones": "ratos", "ratón": "rato", "raton": "rato",
    "botellas": "garrafas", "botella": "garrafa", "tazas": "canecas", "taza": "caneca",
}

# Only whole qualifiers immediately after the recognised product name.
QUALIFIERS = {
    "de hombre": "de homem", "para hombre": "para homem",
    "de mujer": "de senhora", "para mujer": "para senhora",
    "de niño": "de criança", "de niña": "de criança",
    "para niños": "para crianças", "para niñas": "para crianças",
    "para niño": "para criança", "para niña": "para criança",
    "de bebé": "de bebé", "para bebé": "para bebé",
    "de manga corta": "de manga curta", "de manga larga": "de manga comprida",
    "con capucha": "com capuz", "sin capucha": "sem capuz",
    "sin mangas": "sem mangas", "con cremallera": "com fecho de correr",
    "de fútbol": "de futebol", "de futbol": "de futebol",
    "de baloncesto": "de basquetebol", "de entrenamiento": "de treino",
    "de piel": "de pele", "de cuero": "de couro", "de algodón": "de algodão",
    "de algodon": "de algodão", "de lana": "de lã",
    "inalámbricos": "sem fios", "inalambricos": "sem fios",
    "inalámbrico": "sem fios", "inalambrico": "sem fios",
}


def _pattern(glossary: dict[str, str]) -> re.Pattern:
    alternatives = (r"\s+".join(re.escape(word) for word in key.split())
                    for key in sorted(glossary, key=len, reverse=True))
    # Do not match part of a reference, compound name, hashtag or URL.
    return re.compile(r"(?:" + "|".join(alternatives) + r")(?=$|[\s\[\](),:;.!?])", re.I)


_PRODUCT = _pattern(PRODUCTS)
_QUALIFIER = _pattern(QUALIFIERS)


def _replacement(match: re.Match, glossary: dict[str, str]) -> str:
    original = match.group()
    translated = glossary.get(" ".join(original.casefold().split()), original)
    if original.isupper():
        return translated.upper()
    if original[0].isupper():
        return translated[0].upper() + translated[1:]
    return translated


def translate_product_title(title: str) -> str:
    # Keep leading emoji/brackets/whitespace without skipping words or numbers.
    prefix = re.match(r"[^\w#@/]*", title).end()
    match = _PRODUCT.match(title, prefix)
    if not match:
        return title
    parts = [title[:prefix], _replacement(match, PRODUCTS)]
    end = match.end()
    while True:
        gap = re.match(r"\s+", title[end:])
        if not gap:
            break
        qualifier = _QUALIFIER.match(title, end + gap.end())
        if not qualifier:
            break
        parts.extend((gap.group(), _replacement(qualifier, QUALIFIERS)))
        end = qualifier.end()
    return "".join(parts) + title[end:]
