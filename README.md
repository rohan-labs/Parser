# Automating MCQ Data entry onto Supabase

## About
Parsing unstructured PDF / DOCX documents (containing MCQ exam questions) to JSON objects for SQL storage.
- 1 - Run it locally
- 2 - Upload sample document (con 
- 3 - Once parsed to JSON click upload to Supabase

### Run it locally

```bash
# Create a virtual environment
virtualenv .venv

# Activate the virtual environment
source .venv/bin/activate

# Install the required packages
pip install -r requirements.txt

# Run the Streamlit app
streamlit run app.py
