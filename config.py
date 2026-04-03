import os
from dotenv import load_dotenv
load_dotenv()
# ============================================
# PATHS
# ============================================
import os

BASE_DIR   = os.path.join('C:\\', 'Users', 'Harini',
                          'Documents', 'thesis_research', 'PDFBOX-v5')
PDFBOX_DIR = os.path.join(BASE_DIR, 'pdfbox')

GENERATED_TESTS_DIR = os.path.join(PDFBOX_DIR, 'generated_tests')
PROMPTS_DIR         = os.path.join(BASE_DIR, 'prompts')
RESPONSES_DIR       = os.path.join(BASE_DIR, 'responses')
PLANS_DIR           = os.path.join(BASE_DIR, 'plans')
RESULTS_DIR         = os.path.join(BASE_DIR, 'results')
RESULTS_JSON        = os.path.join(RESULTS_DIR, 'results.json')
FINAL_REPORT        = os.path.join(RESULTS_DIR, 'final_report.txt')
INPUT_JSON          = os.path.join(BASE_DIR, 'extracted_metadata_final.json')
TEST_RESOURCES_DIR  = os.path.join(PDFBOX_DIR, 'src', 'test', 'resources')
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# ============================================
# LLM SETTINGS
# ============================================
LLM_MODEL       = 'gpt-4o-mini'
LLM_MAX_TOKENS  = 1500
LLM_TEMPERATURE = 0
API_SLEEP_SEC   = 1
MAX_RETRIES = 2
# ============================================
# MAVEN SETTINGS
# ============================================
TEST_TIMEOUT  = 30
MAVEN_TIMEOUT = 60
# Set this to the full path of mvn.cmd if 'mvn' is not on your terminal's PATH
# e.g. r'C:\Program Files\Maven\apache-maven-3.9.6\bin\mvn.cmd'
MAVEN_EXECUTABLE = r'C:\Program Files\maven\apache-maven-3.9.14-bin\apache-maven-3.9.14\bin\mvn.cmd'
# Set this to your JDK root folder if JAVA_HOME is not set in your terminal
# e.g. r'C:\Program Files\Java\jdk-21'
JAVA_HOME = r'C:\Program Files\Java\ms-25.0.2'