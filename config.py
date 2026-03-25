import os
from dotenv import load_dotenv
load_dotenv()
# ============================================
# PATHS
# ============================================
BASE_DIR   = '/Users/apple/Documents/research/thesis_research/v5'
PDFBOX_DIR = f'{BASE_DIR}/pdfbox'
INPUT_JSON = f'{BASE_DIR}/extracted_metadata_final.json'

GENERATED_TESTS_DIR = f'{PDFBOX_DIR}/generated_tests'
PROMPTS_DIR         = f'{BASE_DIR}/prompts'
RESPONSES_DIR       = f'{BASE_DIR}/responses'
RESULTS_DIR         = f'{BASE_DIR}/results'
RESULTS_JSON        = f'{RESULTS_DIR}/results.json'
FINAL_REPORT        = f'{RESULTS_DIR}/final_report.txt'
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