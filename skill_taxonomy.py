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


# Groups of skills that postings commonly present as alternatives.
# If two or more from the same group are found, they get collapsed into
# a single "A/B/C" entry rather than listed as separate requirements.
ALTERNATIVE_GROUPS = [
    {"React", "Angular", "Vue"},
    {"AWS", "Azure", "GCP"},
    {"PostgreSQL", "MySQL", "Oracle", "SQL Server"},
    {"Java", "Python", "C#", "C++"},
]


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


def group_alternatives(skills: list[str]) -> list[str]:
    """Collapse known interchangeable skills into one 'A/B' entry.

    Honest limitation: this assumes any two skills from the same group
    are alternatives, which isn't always true — a posting could
    genuinely require both AWS and Azure. Plain text matching can't
    tell the difference; only reading the sentence could."""
    remaining = list(skills)
    result = []

    for group in ALTERNATIVE_GROUPS:
        matched = [s for s in remaining if s in group]
        if len(matched) > 1:
            result.append("/".join(matched))
            remaining = [s for s in remaining if s not in matched]

    return result + remaining


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
