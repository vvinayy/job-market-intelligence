"""
Taxonomy-based skill extraction — no AI model involved.

How it works, in plain terms:
  1. A fixed vocabulary maps every spelling variant to one canonical name.
     ("reactjs", "react.js", "react" all -> "React")
  2. For each job description, check which of those variants appear in
     the text, using word-boundary matching so "R" doesn't match inside
     "React" and "Go" doesn't match inside "Google".
  3. Return the canonical names found, de-duplicated.

Scope is deliberately narrow: IT/software roles only. Adding every skill
in existence would mostly add false positives.

Trade-off worth knowing: this finds only what's on the list. A framework
released last month, or one nobody added yet, will be silently missed.
That's the cost of not using a model.
"""

import re


# ---------------------------------------------------------------------
# The vocabulary. Key = canonical name shown in output.
# Value = every spelling that should map to it.
# ---------------------------------------------------------------------
SKILL_ALIASES = {
    # Languages
    "Python": ["python"],
    "Java": ["java"],
    "JavaScript": ["javascript", "java script"],
    "TypeScript": ["typescript"],
    "C": ["c language", "c programming"],
    "C++": [r"c\+\+", "cpp"],
    "C#": [r"c#", "c sharp", "csharp"],
    "Go": ["golang", "go language"],
    "Rust": ["rust"],
    "Ruby": ["ruby"],
    "PHP": ["php"],
    "Scala": ["scala"],
    "Kotlin": ["kotlin"],
    "Swift": ["swift"],
    "R": ["r programming", "r language"],
    "SQL": ["sql"],
    "Shell scripting": ["bash", "shell scripting", "shell script"],
    "PowerShell": ["powershell"],

    # Frontend
    "React": ["react", "reactjs", r"react\.js", "react js"],
    "Angular": ["angular", "angularjs", r"angular\.js", r"angular \d+\+?"],
    "Vue": ["vue", "vuejs", r"vue\.js"],
    "Next.js": [r"next\.js", "nextjs"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3"],
    "Tailwind": ["tailwind", "tailwindcss"],
    "Bootstrap": ["bootstrap"],
    "jQuery": ["jquery"],
    "Redux": ["redux"],

    # Backend / frameworks
    "Node.js": [r"node\.js", "nodejs", "node js"],
    "Express": ["express", r"express\.js", "expressjs"],
    "Django": ["django"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi", "fast api"],
    "Spring Boot": ["spring boot", "springboot"],
    "Spring": ["spring framework"],
    ".NET": [r"\.net", "dotnet", "microsoft .net"],
    ".NET Core": [r"\.net core", "dotnet core", r"\.net \(core\)"],
    "ASP.NET": [r"asp\.net"],
    "Entity Framework": ["entity framework"],
    "MVC": ["mvc"],
    "LINQ": ["linq"],
    "Web API": ["web api"],
    "REST API": ["rest api", "restful api", "rest apis", "restful"],
    "GraphQL": ["graphql"],
    "gRPC": ["grpc"],
    "Microservices": ["microservices", "micro services"],
    "Hibernate": ["hibernate"],
    "JPA": ["jpa", "java persistence api"],
    "Distributed Systems": ["distributed systems", "distributed system"],

    # Databases
    "NoSQL": ["nosql", "no sql", "nosql databases"],
    "PostgreSQL": ["postgresql", "postgres", "psql"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongodb", "mongo db"],
    "Redis": ["redis"],
    "Oracle": ["oracle db", "oracle database"],
    "SQL Server": ["sql server", "mssql", "ms sql"],
    "Cassandra": ["cassandra"],
    "Elasticsearch": ["elasticsearch", "elastic search"],
    "DynamoDB": ["dynamodb"],
    "Snowflake": ["snowflake"],

    # Cloud
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure", "microsoft azure"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Lambda": ["aws lambda"],
    "S3": ["amazon s3", r"aws s3"],
    "EC2": ["ec2"],

    # DevOps / infra
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Terraform": ["terraform"],
    "Ansible": ["ansible"],
    "Jenkins": ["jenkins"],
    "CI/CD": [r"ci/cd", "ci cd", "continuous integration"],
    "Git": ["git"],
    "GitHub": ["github"],
    "GitLab": ["gitlab"],
    "Linux": ["linux", "unix"],
    "Nginx": ["nginx"],
    "Kafka": ["kafka", "apache kafka"],
    "RabbitMQ": ["rabbitmq"],
    # Missed until a posting listed "Redis, Celery, Kafka, or microservices
    # architecture" and the detector built the choice set without it.
    "Celery": ["celery"],
    "Airflow": ["airflow", "apache airflow"],
    "Spark": ["spark", "apache spark", "pyspark"],
    "Hadoop": ["hadoop"],

    # Data / ML
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "TensorFlow": ["tensorflow"],
    "PyTorch": ["pytorch"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "LangChain": ["langchain", "lang chain"],
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "Microsoft Fabric": ["microsoft fabric", "ms fabric"],
    "Databricks": ["databricks"],
    "Synapse": ["synapse", "azure synapse"],
    "Data Factory": ["data factory", "azure data factory", "adf"],
    "dbt": ["dbt"],
    "Looker": ["looker"],
    "BigQuery": ["bigquery", "big query"],
    "Redshift": ["redshift"],
    "Delta Lake": ["delta lake"],
    "Data Warehouse": ["data warehouse", "datawarehouse", "warehousing"],
    "ETL": ["etl", "elt"],
    # Found by using hirist's own tags as ground truth: a skill the employer
    # declared AND wrote into the description, that this table could not find.
    # Frequencies are share of 591 stored descriptions. Concepts that turned
    # up the same way -- Design Patterns, Multithreading, OOPS -- are left out,
    # matching the existing exclusion of Agile, Coding, Testing and Debugging.
    "Data Engineering": ["data engineering"],          # 14%
    "LLM": ["llm", "llms", "large language model", "large language models"],  # 13%
    "Generative AI": ["generative ai", "gen ai", "genai"],  # 12%
    "Big Data": ["big data"],                          # 7%
    "Data Modeling": ["data modeling", "data modelling"],  # 7%
    "Data Ingestion": ["data ingestion"],              # 4%
    "Data Pipeline": ["data pipeline", "data pipelines"],  # 3%
    "Data Integration": ["data integration"],          # 3%
    "IICS": ["iics"],                                  # niche, unambiguous
    "Matillion": ["matillion"],
    "Solr": ["solr", "apache solr"],
    "OpenSearch": ["opensearch", "open search"],
    "Lucene": ["lucene", "apache lucene"],

    # Testing
    "NUnit": ["nunit"],
    "JUnit": ["junit"],
    "pytest": ["pytest"],
    "Selenium": ["selenium"],
    "Jest": ["jest"],
}


def _build_pattern(alias: str) -> re.Pattern:
    """Word-boundary match so 'R' doesn't fire inside 'React', and
    'Go' doesn't fire inside 'Google'. Aliases containing regex
    metacharacters (like C++ or .NET) are pre-escaped in the table."""
    return re.compile(rf"(?<![a-zA-Z0-9]){alias}(?![a-zA-Z0-9])", re.IGNORECASE)


_TOKEN = re.compile(r"[A-Za-z0-9+#.]+")


def _tokenise(text: str) -> list[str]:
    """Split text into comparable tokens.

    The aliases go through this too, so punctuation can never split a term on
    one side and not the other: "CI/CD" in prose and the alias "ci/cd" both
    become ["ci", "cd"]. '+', '#' and '.' are kept inside a token so C++, C#,
    .NET and Node.js survive whole."""
    return [t for t in (m.group(0).lower().strip(".")
                        for m in _TOKEN.finditer(text)) if t]


# Built once at import. Scanning every description once per pattern meant 176
# full passes over the text; tokenising once and looking each 1-3 word window
# up in a dict makes the cost proportional to the description rather than to
# the vocabulary — so adding skills is now free. Measured 6.4ms -> 0.75ms per
# description over 591 real postings.
_REGEX_META = set(r"^$*?{}[]\|()")
_LITERAL_ALIASES: dict[str, str] = {}
_REGEX_ALIASES: list[tuple[str, re.Pattern]] = []

for _canonical, _aliases in SKILL_ALIASES.items():
    for _alias in _aliases:
        # Aliases carrying regex metacharacters stay on the pattern path.
        # That is not only the version matchers: ".NET" is stored pre-escaped
        # as "\.net", and the backslash keeps it here — which is what stops
        # the token "net" in ordinary prose ("net effect") from matching it.
        if any(ch in _REGEX_META for ch in _alias):
            _REGEX_ALIASES.append((_canonical, _build_pattern(_alias)))
        else:
            _LITERAL_ALIASES.setdefault(" ".join(_tokenise(_alias)), _canonical)

_MAX_ALIAS_TOKENS = max(len(key.split()) for key in _LITERAL_ALIASES)
_CANONICAL_ORDER = {name: i for i, name in enumerate(SKILL_ALIASES)}


def extract_skills(description: str) -> list[str]:
    """Find every taxonomy skill mentioned in the text."""
    if not description:
        return []

    tokens = _tokenise(description)
    found: set[str] = set()
    for width in range(1, _MAX_ALIAS_TOKENS + 1):
        for i in range(len(tokens) - width + 1):
            canonical = _LITERAL_ALIASES.get(" ".join(tokens[i:i + width]))
            if canonical:
                found.add(canonical)

    for canonical, pattern in _REGEX_ALIASES:
        if canonical not in found and pattern.search(description):
            found.add(canonical)

    # Declaration order, as the pattern loop returned before it.
    return sorted(found, key=_CANONICAL_ORDER.get)


# ---------------------------------------------------------------------
# Certifications — a different kind of signal from a tool or language
# (a credential someone holds, not something they use day to day), so
# kept as its own vocabulary rather than folded into SKILL_ALIASES.
# ---------------------------------------------------------------------
CERTIFICATION_ALIASES = {
    "AWS Certified": ["aws certified", "certified aws"],
    "Azure Certified": ["azure certified", "microsoft certified: azure",
                         "az-900", "az-104", "az-204", "az-305"],
    "GCP Certified": ["gcp certified", "google cloud certified"],
    "PMP": ["pmp", "project management professional"],
    "CSM": ["csm", "certified scrummaster", "certified scrum master"],
    "CKA": ["cka", "certified kubernetes administrator"],
    "CISSP": ["cissp"],
    "ITIL": ["itil"],
    "Six Sigma": ["six sigma"],
    "CompTIA Security+": [r"security\+", "comptia security"],
    "SAFe": ["safe agilist", "scaled agile"],
}

_COMPILED_CERTS = [
    (canonical, _build_pattern(alias))
    for canonical, aliases in CERTIFICATION_ALIASES.items()
    for alias in aliases
]


def extract_certifications(description: str) -> list[str]:
    """Find every known certification mentioned in the text. Same
    word-boundary matching as extract_skills(), separate vocabulary."""
    if not description:
        return []
    found = []
    for canonical, pattern in _COMPILED_CERTS:
        if canonical not in found and pattern.search(description):
            found.append(canonical)
    return found


# =====================================================================
# SKILL CHOICE GROUPS — which of a posting's skills are alternatives
# ("AWS, Azure, or GCP") rather than all required together. Reads the
# sentence; a posting can genuinely require both AWS and Azure.
#
# Two rules, derived by parsing real postings with spaCy and reading the
# structures it produced. spaCy is NOT a dependency — it was a measuring
# instrument. The rules need only commas and "and"/"or", so this runs in
# ~0.4 ms per posting with nothing installed:
#
#   1. "or" INSIDE a comma segment binds tightly, joining just the nearest
#      term either side.  "React JS using Nginx or Apache" -> Nginx|Apache
#
#   2. "or" straight after a comma is list-final and distributes backwards,
#      stopping at (but including) a segment introduced by "and".
#      "Git, Maven or Gradle, Docker, and AWS, Azure, or GCP"
#        -> Maven|Gradle and AWS|Azure|GCP, leaving Git and Docker out.
#
# Scoped to the skills already found on this posting, not the whole
# taxonomy: scanning all of it dragged in ordinary English words and cost
# ~700x more time.
# =====================================================================
_OR_RE = re.compile(r"\bor\b", re.IGNORECASE)
_AND_LEAD_RE = re.compile(r"^\s*(?:and|&)\b", re.IGNORECASE)

# Words that close the list. Without them the scan runs past the end:
# "Maven, Gradle, or similar tools FOR Java/Node" pulled in Java.
_BOUNDARY_RE = re.compile(
    r"\b(?:for|in|on|with|within|across|using|to|from|at|by|based|"
    r"including|include|such as|like|e\.g\.?)\b", re.IGNORECASE)


def _clip_after(text: str) -> str:
    """Right of the conjunction: keep up to the first boundary word."""
    m = _BOUNDARY_RE.search(text)
    return text[:m.start()] if m else text


def _clip_before(text: str) -> str:
    """Left of the conjunction: keep what follows the last boundary word,
    so "Proficiency in Python" -> "Python"."""
    last = None
    for m in _BOUNDARY_RE.finditer(text):
        last = m
    return text[last.end():] if last else text


def _make_resolver(skill_names: list[str], blocklist: set[str]):
    """Resolver closed over one posting's own skills. Longest name first
    so "SQL Server" wins over "SQL".

    Single-character names are kept, not filtered: "R" and "C" are real
    languages, and the word-boundary match already stops them firing
    inside "for" or "React". The genuinely dangerous short fragments
    ("S", "As", "Be", "Do") are handled by the blocklist instead, which
    is reversible and inspectable -- a length rule would silently drop
    every future one-letter language too."""
    pairs = sorted({n.lower(): n for n in skill_names}.items(),
                   key=lambda kv: -len(kv[0]))
    pats = [(re.compile(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])"), disp)
            for k, disp in pairs if k not in blocklist]

    def resolve(text: str, which: str = "first") -> str | None:
        low, hits = text.lower(), []
        for pat, disp in pats:
            for m in pat.finditer(low):
                # skip a match sitting inside one already claimed by a
                # longer name ("SQL" inside "SQL Server")
                if not any(s <= m.start() < s + ln for s, _, ln in hits):
                    hits.append((m.start(), disp, len(m.group(0))))
        if not hits:
            return None
        hits.sort(key=lambda h: h[0])
        return hits[-1][1] if which == "last" else hits[0][1]

    return resolve


def _groups_in_sentence(sentence: str, resolve) -> list[tuple[str, ...]]:
    segments, pos = [], 0
    for m in re.finditer(r",", sentence):
        segments.append(sentence[pos:m.start()])
        pos = m.end()
    segments.append(sentence[pos:])

    groups: list[tuple[str, ...]] = []
    for idx, segment in enumerate(segments):
        for m in _OR_RE.finditer(segment):
            before, after = segment[:m.start()], segment[m.end():]

            if before.strip():
                # RULE 1 — tight binding. Nearest term left is the LAST named,
                # right is the FIRST; "last" on both mismatched the pair.
                left = resolve(_clip_before(before), "last")
                right = resolve(_clip_after(after), "first")
                if left and right and left != right:
                    groups.append((left, right))
                continue

            # RULE 2 — list-final "or", distributing backwards. A tail naming
            # nothing known ("or equivalent") still leaves the rest a choice.
            tail = resolve(_clip_after(after), "first")
            members = [tail] if tail else []
            for previous in reversed(segments[:idx]):
                name = resolve(_clip_before(previous), "last")
                if name and name not in members:
                    members.append(name)
                if _AND_LEAD_RE.match(previous):
                    break            # include this segment, then stop
            if len(members) >= 2:
                groups.append(tuple(reversed(members)))

    # drop any group wholly contained in a larger one from this sentence
    return [tuple(dict.fromkeys(g)) for g in groups
            if not any(set(g) < set(h) for h in groups)]


def find_skill_choice_groups(description: str | None,
                             skill_names: list[str],
                             blocklist: set[str] | None = None) -> list[tuple[str, ...]]:
    """Which of `skill_names` this description presents as alternatives.

    Returns a list of tuples, each naming two or more skills where the
    posting asks for any ONE of them. Skills absent from every tuple are
    required in the ordinary way, so an empty result means "no choices
    found" -- not "all skills verified as individually required".

    Heuristic, like split_description_sections(): roughly three in four
    groups are right, and it finds a choice in about a third of postings.
    It never invents a skill -- every name returned is one that was
    passed in."""
    if not description or len(skill_names) < 2:
        return []

    resolve = _make_resolver(skill_names, {b.lower() for b in (blocklist or set())})
    found: list[tuple[str, ...]] = []
    for chunk in re.split(r"\n+", description):
        for sentence in re.split(r"(?<=[.;!?])\s+", " ".join(chunk.split())):
            if " or " not in sentence.lower():
                continue
            found += _groups_in_sentence(sentence, resolve)
    return list(dict.fromkeys(found))


def extract_experience(description: str) -> tuple[int | None, int | None]:
    """Pull a years-of-experience range from common phrasings:
    '4-7 years', '5+ years', 'minimum 3 years', '2 to 5 yrs'."""
    if not description:
        return None, None

    # Range: "4-7 years", "2 to 5 yrs"
    range_match = re.search(
        r"(\d{1,2})\s*(?:-|to|–)\s*(\d{1,2})\s*\+?\s*(?:years|yrs|year)",
        description, re.IGNORECASE
    )
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))

    # Minimum only: "5+ years", "minimum 3 years", "at least 4 years"
    min_match = re.search(
        r"(?:minimum|at least|min\.?)?\s*(\d{1,2})\s*\+\s*(?:years|yrs|year)"
        r"|(?:minimum|at least|min\.?)\s+(\d{1,2})\s*(?:years|yrs|year)",
        description, re.IGNORECASE
    )
    if min_match:
        value = min_match.group(1) or min_match.group(2)
        return int(value), None

    return None, None
