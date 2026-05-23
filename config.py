import os
from dotenv import load_dotenv
load_dotenv()
# ============================================
# PATHS
# ============================================
import os

BASE_DIR   = os.path.join('C:\\', 'Users', 'Harini',
                          'Documents', 'thesis_research', 'PDFBOX-v5')
# Maven build dir for THIS pipeline. The PDFBOX-v5 copy above is in use by another
# pipeline, so we compile/run tests in a separate pdfbox checkout. PDFBOX_REPO is the
# multi-module repo root; PDFBOX_DIR is the nested `pdfbox` Maven module.
PDFBOX_REPO = os.path.join('C:\\', 'Users', 'Harini', 'Documents',
                           'thesis_research', 'testgenerator_v1', 'pdfbox')
PDFBOX_DIR  = os.path.join(PDFBOX_REPO, 'pdfbox')   # actual pdfbox Maven module
GENRATED_FILES = os.path.join(BASE_DIR, 'generated_files/gpt5mini-v1')
GENERATED_TESTS_DIR = os.path.join(PDFBOX_DIR, 'generated_testsgpt5mini_v1')
PROMPTS_DIR         = os.path.join(GENRATED_FILES, 'prompts')
RESPONSES_DIR       = os.path.join(GENRATED_FILES, 'responses')
RESULTS_DIR         = os.path.join(GENRATED_FILES, 'results')
RESULTS_JSON        = os.path.join(RESULTS_DIR, 'results.json')
FINAL_REPORT        = os.path.join(RESULTS_DIR, 'final_report.txt')
INPUT_JSON          = os.path.join(BASE_DIR, 'extracted_metadata_final.json')
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# ============================================
# LLM SETTINGS
# ============================================
LLM_MODEL       = 'gpt-5-mini'
# gpt-5 / o-series are reasoning models: this budget is shared by hidden reasoning
# tokens AND the visible output, so keep it generous (sent as max_completion_tokens).
LLM_MAX_TOKENS  = 16384
# Reasoning models only allow the default temperature, so this is IGNORED for gpt-5
# (handled in llm_client.py). Kept for non-reasoning fallback models.
LLM_TEMPERATURE = 0
# gpt-5 reasoning effort: 'minimal' | 'low' | 'medium' | 'high'.
LLM_REASONING_EFFORT = 'low'
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