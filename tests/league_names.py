"""
Real club names from both feeds, captured for 2025/26.

The left-hand side of each pair is how football-data.org spells a club; the
right-hand side is how football-data.co.uk spells the same club in that
division's results file. These are not invented examples — they are the two
lists the resolver actually has to reconcile, and they exist here so the
resolver can be tested without a network, on data that is known to be right.

The KNOWN sets are the results-file spellings, taken verbatim from the
2025/26 CSVs. Historic seasons add more names on a real deployment; a smaller
set is the harder test, because it gives the fuzzy stage fewer near-misses to
reject.
"""

KNOWN = {
    "E0": [
        "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton",
        "Burnley", "Chelsea", "Crystal Palace", "Everton", "Fulham", "Leeds",
        "Liverpool", "Man City", "Man United", "Newcastle", "Nott'm Forest",
        "Sunderland", "Tottenham", "West Ham", "Wolves",
    ],
    "E1": [
        "Birmingham", "Blackburn", "Bristol City", "Charlton", "Coventry",
        "Derby", "Hull", "Ipswich", "Leicester", "Middlesbrough", "Millwall",
        "Norwich", "Oxford", "Portsmouth", "Preston", "QPR", "Sheffield United",
        "Sheffield Weds", "Southampton", "Stoke", "Swansea", "Watford",
        "West Brom", "Wrexham",
    ],
    "SP1": [
        "Alaves", "Ath Bilbao", "Ath Madrid", "Barcelona", "Betis", "Celta",
        "Elche", "Espanol", "Getafe", "Girona", "Levante", "Mallorca",
        "Osasuna", "Oviedo", "Real Madrid", "Sevilla", "Sociedad", "Valencia",
        "Vallecano", "Villarreal",
    ],
    "I1": [
        "Atalanta", "Bologna", "Cagliari", "Como", "Cremonese", "Fiorentina",
        "Genoa", "Inter", "Juventus", "Lazio", "Lecce", "Milan", "Napoli",
        "Parma", "Pisa", "Roma", "Sassuolo", "Torino", "Udinese", "Verona",
    ],
    "D1": [
        "Augsburg", "Bayern Munich", "Dortmund", "Ein Frankfurt", "FC Koln",
        "Freiburg", "Hamburg", "Heidenheim", "Hoffenheim", "Leverkusen",
        "M'gladbach", "Mainz", "RB Leipzig", "St Pauli", "Stuttgart",
        "Union Berlin", "Werder Bremen", "Wolfsburg",
    ],
    "F1": [
        "Angers", "Auxerre", "Brest", "Le Havre", "Lens", "Lille", "Lorient",
        "Lyon", "Marseille", "Metz", "Monaco", "Nantes", "Nice", "Paris FC",
        "Paris SG", "Rennes", "Strasbourg", "Toulouse",
    ],
    "P1": [
        "Alverca", "Arouca", "AVS", "Benfica", "Boavista", "Casa Pia",
        "Estoril", "Estrela", "Famalicao", "Gil Vicente", "Guimaraes",
        "Moreirense", "Nacional", "Porto", "Rio Ave", "Santa Clara",
        "Sp Braga", "Sp Lisbon", "Tondela",
    ],
    # Observed, not guessed: every name football-data.co.uk has used for this
    # division across eleven seasons, read off the CSVs themselves.
    "N1": [
        "AZ Alkmaar", "Ajax", "Almere City", "Cambuur", "Den Haag",
        "Excelsior", "FC Emmen", "Feyenoord", "For Sittard", "Go Ahead Eagles",
        "Graafschap", "Groningen", "Heerenveen", "Heracles", "NAC Breda",
        "Nijmegen", "PSV Eindhoven", "Roda", "Sparta Rotterdam", "Telstar",
        "Twente", "Utrecht", "VVV Venlo", "Vitesse", "Volendam", "Waalwijk",
        "Willem II", "Zwolle",
    ],
}

FEED = {
    "E0": {
        "Arsenal FC": "Arsenal", "Aston Villa FC": "Aston Villa",
        "AFC Bournemouth": "Bournemouth", "Brentford FC": "Brentford",
        "Brighton & Hove Albion FC": "Brighton", "Burnley FC": "Burnley",
        "Chelsea FC": "Chelsea", "Crystal Palace FC": "Crystal Palace",
        "Everton FC": "Everton", "Fulham FC": "Fulham",
        "Leeds United FC": "Leeds", "Liverpool FC": "Liverpool",
        "Manchester City FC": "Man City", "Manchester United FC": "Man United",
        "Newcastle United FC": "Newcastle",
        "Nottingham Forest FC": "Nott'm Forest",
        "Sunderland AFC": "Sunderland", "Tottenham Hotspur FC": "Tottenham",
        "West Ham United FC": "West Ham",
        "Wolverhampton Wanderers FC": "Wolves",
    },
    "E1": {
        "Birmingham City FC": "Birmingham", "Blackburn Rovers FC": "Blackburn",
        "Bristol City FC": "Bristol City", "Charlton Athletic FC": "Charlton",
        "Coventry City FC": "Coventry", "Derby County FC": "Derby",
        "Hull City AFC": "Hull", "Ipswich Town FC": "Ipswich",
        "Leicester City FC": "Leicester", "Middlesbrough FC": "Middlesbrough",
        "Millwall FC": "Millwall", "Norwich City FC": "Norwich",
        "Oxford United FC": "Oxford", "Portsmouth FC": "Portsmouth",
        "Preston North End FC": "Preston",
        "Queens Park Rangers FC": "QPR",
        "Sheffield United FC": "Sheffield United",
        "Sheffield Wednesday FC": "Sheffield Weds",
        "Southampton FC": "Southampton", "Stoke City FC": "Stoke",
        "Swansea City AFC": "Swansea", "Watford FC": "Watford",
        "West Bromwich Albion FC": "West Brom", "Wrexham AFC": "Wrexham",
    },
    "SP1": {
        "Deportivo Alavés": "Alaves", "Athletic Club": "Ath Bilbao",
        "Club Atlético de Madrid": "Ath Madrid", "FC Barcelona": "Barcelona",
        "Real Betis Balompié": "Betis", "RC Celta de Vigo": "Celta",
        "Elche CF": "Elche", "RCD Espanyol de Barcelona": "Espanol",
        "Getafe CF": "Getafe", "Girona FC": "Girona", "Levante UD": "Levante",
        "RCD Mallorca": "Mallorca", "CA Osasuna": "Osasuna",
        "Real Oviedo": "Oviedo", "Real Madrid CF": "Real Madrid",
        "Sevilla FC": "Sevilla", "Real Sociedad de Fútbol": "Sociedad",
        "Valencia CF": "Valencia", "Rayo Vallecano de Madrid": "Vallecano",
        "Villarreal CF": "Villarreal",
    },
    "I1": {
        "Atalanta BC": "Atalanta", "Bologna FC 1909": "Bologna",
        "Cagliari Calcio": "Cagliari", "Como 1907": "Como",
        "US Cremonese": "Cremonese", "ACF Fiorentina": "Fiorentina",
        "Genoa CFC": "Genoa", "FC Internazionale Milano": "Inter",
        "Juventus FC": "Juventus", "SS Lazio": "Lazio", "US Lecce": "Lecce",
        "AC Milan": "Milan", "SSC Napoli": "Napoli",
        "Parma Calcio 1913": "Parma", "Pisa Sporting Club": "Pisa",
        "AS Roma": "Roma", "US Sassuolo Calcio": "Sassuolo",
        "Torino FC": "Torino", "Udinese Calcio": "Udinese",
        "Hellas Verona FC": "Verona",
    },
    "D1": {
        "FC Augsburg": "Augsburg", "FC Bayern München": "Bayern Munich",
        "Borussia Dortmund": "Dortmund", "Eintracht Frankfurt": "Ein Frankfurt",
        "1. FC Köln": "FC Koln", "SC Freiburg": "Freiburg",
        "Hamburger SV": "Hamburg", "1. FC Heidenheim 1846": "Heidenheim",
        "TSG 1899 Hoffenheim": "Hoffenheim", "Bayer 04 Leverkusen": "Leverkusen",
        "Borussia Mönchengladbach": "M'gladbach", "1. FSV Mainz 05": "Mainz",
        "RB Leipzig": "RB Leipzig", "FC St. Pauli 1910": "St Pauli",
        "VfB Stuttgart": "Stuttgart", "1. FC Union Berlin": "Union Berlin",
        "SV Werder Bremen": "Werder Bremen", "VfL Wolfsburg": "Wolfsburg",
    },
    "F1": {
        "Angers SCO": "Angers", "AJ Auxerre": "Auxerre",
        "Stade Brestois 29": "Brest", "Le Havre AC": "Le Havre",
        "Racing Club de Lens": "Lens", "LOSC Lille": "Lille",
        "FC Lorient": "Lorient", "Olympique Lyonnais": "Lyon",
        "Olympique de Marseille": "Marseille", "FC Metz": "Metz",
        "AS Monaco FC": "Monaco", "FC Nantes": "Nantes", "OGC Nice": "Nice",
        "Paris FC": "Paris FC", "Paris Saint-Germain FC": "Paris SG",
        "Stade Rennais FC 1901": "Rennes", "RC Strasbourg Alsace": "Strasbourg",
        "Toulouse FC": "Toulouse",
    },
    "P1": {
        "FC Alverca": "Alverca", "FC Arouca": "Arouca",
        "AVS Futebol SAD": "AVS", "SL Benfica": "Benfica",
        "Boavista FC": "Boavista", "Casa Pia AC": "Casa Pia",
        "GD Estoril Praia": "Estoril", "CF Estrela da Amadora": "Estrela",
        "FC Famalicão": "Famalicao", "Gil Vicente FC": "Gil Vicente",
        "Vitória SC": "Guimaraes", "Moreirense FC": "Moreirense",
        "CD Nacional": "Nacional", "FC Porto": "Porto", "Rio Ave FC": "Rio Ave",
        "CD Santa Clara": "Santa Clara", "SC Braga": "Sp Braga",
        "Sporting CP": "Sp Lisbon", "CD Tondela": "Tondela",
    },
    # Observed on 27 Aug 2026 by scripts/check_names.py against the live
    # football-data.org feed. Three of these are NOT what a reasonable person
    # would guess — "NEC", "SBV Excelsior", "Telstar 1963" — which is why this
    # table is now transcribed from the feed rather than written from memory.
    "N1": {
        "ADO Den Haag": "Den Haag", "AFC Ajax": "Ajax", "AZ": "AZ Alkmaar",
        "FC Groningen": "Groningen", "FC Twente '65": "Twente",
        "FC Utrecht": "Utrecht", "Feyenoord Rotterdam": "Feyenoord",
        "Fortuna Sittard": "For Sittard",
        "Go Ahead Eagles": "Go Ahead Eagles", "NEC": "Nijmegen",
        "PEC Zwolle": "Zwolle", "PSV": "PSV Eindhoven",
        "SBV Excelsior": "Excelsior", "SC Cambuur-Leeuwarden": "Cambuur",
        "SC Heerenveen": "Heerenveen", "Sparta Rotterdam": "Sparta Rotterdam",
        "Telstar 1963": "Telstar", "Willem II Tilburg": "Willem II",
    },
}
