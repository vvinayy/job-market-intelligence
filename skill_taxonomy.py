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

    # Databases
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


# Compile once at import, not per description — this matters when you're
# processing hundreds of postings.
_COMPILED = [
    (canonical, _build_pattern(alias))
    for canonical, aliases in SKILL_ALIASES.items()
    for alias in aliases
]


def extract_skills(description: str) -> list[str]:
    """Find every taxonomy skill mentioned in the text."""
    if not description:
        return []

    found = []
    for canonical, pattern in _COMPILED:
        if canonical not in found and pattern.search(description):
            found.append(canonical)
    return found


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
# ("AWS, Azure, or GCP") rather than all required together.
#
# This replaces an earlier group_alternatives(), which assumed any two
# skills from a fixed list were alternatives. Its own docstring named
# the flaw: a posting can genuinely require both AWS and Azure, and
# "only reading the sentence could" tell the difference. This does read
# the sentence.
#
# The scoping rule below was derived by parsing real postings with a
# dependency parser (spaCy) and reading which structures it produced.
# The parser is NOT a dependency here -- it was a measuring instrument.
# The rule it revealed turns out to need only commas and the words
# "and"/"or", so it runs in ~0.4 ms per posting with nothing installed:
#
#   1. "or" INSIDE a comma segment binds tightly, joining just the
#      nearest term either side.
#        "React JS using Nginx or Apache"  ->  Nginx | Apache
#      Naive matching produced React|Vue here -- the "or" is nowhere
#      near them.
#
#   2. "or" immediately after a comma is list-final and distributes
#      back over the preceding segments, stopping at (but including) a
#      segment introduced by "and":
#        "Git, Maven or Gradle, Docker, CI/CD, and AWS, Azure, or GCP"
#          ->  Maven|Gradle  and  AWS|Azure|GCP
#      Git, Docker and CI/CD are correctly left out of both.
#
# Deliberately scoped to the skills ALREADY found on the posting rather
# than the whole taxonomy: extract_skills() has done that job, and
# scanning prose against every known skill both dragged in fragments
# that are ordinary English words and cost ~700x more time.
# =====================================================================
_OR_RE = re.compile(r"\bor\b", re.IGNORECASE)
_AND_LEAD_RE = re.compile(r"^\s*(?:and|&)\b", re.IGNORECASE)

# Words that close the list and begin a new phrase. Without this the
# scan runs past the end of the disjunction and grabs an unrelated
# skill: "Maven, Gradle, or similar tools FOR Java/Node/Python" pulled
# in Java as though it were a third build tool.
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
                # RULE 1 -- tight binding. Direction matters: the nearest
                # term on the left is the LAST named, on the right the
                # FIRST. Using "last" on both turned "Python or R for
                # data processing" into Python|Visualization.
                left = resolve(_clip_before(before), "last")
                right = resolve(_clip_after(after), "first")
                if left and right and left != right:
                    groups.append((left, right))
                continue

            # RULE 2 -- list-final "or", distributing backwards. A tail
            # naming nothing known ("or similar tools", "or equivalent")
            # still leaves the earlier members alternatives to each
            # other, so keep what resolves rather than dropping the list.
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
