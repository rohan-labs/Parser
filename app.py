import os
import json
import streamlit as st
from openai import OpenAI
from supabase import create_client, Client
from io import StringIO
from tempfile import NamedTemporaryFile
import time
import PyPDF2
import docx2txt
from dotenv import load_dotenv

# Client / credential set-up

load_dotenv()

def get_env_variable(var_name):
  try:
      return st.secrets[var_name]
  except Exception:
      return os.getenv(var_name)

openai_api_key = get_env_variable("OPENAI_API_KEY")
supabase_url = get_env_variable("SUPABASE_URL")
supabase_key = get_env_variable("SUPABASE_KEY")
assistant_id = get_env_variable("ASSISTANT_ID")

if not openai_api_key or not supabase_url or not supabase_key or not assistant_id:
  st.error("API keys, credentials, or assistant ID are not properly set.")
  st.stop()

client = OpenAI(api_key=openai_api_key)

st.session_state.assistant = client.beta.assistants.retrieve(assistant_id)

if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input
if prompt := st.chat_input("What is your question?"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Create a new thread for each conversation
    thread = client.beta.threads.create()

    # Add user message to the thread
    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=prompt
    )

    # Run the assistant
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=st.session_state.assistant.id,
    )

    # Wait for the run to complete
    while run.status != "completed":
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

    # Retrieve and display the assistant's response
    messages = client.beta.threads.messages.list(thread_id=thread.id)
    assistant_message = messages.data[0].content[0].text.value

    st.session_state.messages.append({"role": "assistant", "content": assistant_message})
    with st.chat_message("assistant"):
        st.markdown(assistant_message)

# Initialize Supabase client
supabase: Client = create_client(supabase_url, supabase_key)

# Streamlit app
st.title("File Uploader and Parser")

st.write("""
This app allows you to upload multiple PDF, DOCX, or TXT files.
It will parse the content using the OpenAI API into a specific CSV format
and upload the data directly to Supabase.
""")

uploaded_files = st.file_uploader(
  "Upload PDF, DOCX, or TXT files", type=["pdf", "docx", "txt"], accept_multiple_files=True
)

if uploaded_files:
  data_list = []
  any_errors = False

  for uploaded_file in uploaded_files:
      file_name = uploaded_file.name
      st.write(f"Processing **{file_name}**...")

      # Read the file content based on its type
      try:
          if uploaded_file.type == "application/pdf":
              # For PDFs
              with NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
                  temp_pdf.write(uploaded_file.read())
                  temp_pdf.flush()
                  reader = PyPDF2.PdfReader(temp_pdf.name)
                  text_content = ""
                  for page in reader.pages:
                      text_content += page.extract_text()
          elif uploaded_file.type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
              # For DOCX files
              with NamedTemporaryFile(delete=False, suffix=".docx") as temp_docx:
                  temp_docx.write(uploaded_file.read())
                  temp_docx.flush()
                  text_content = docx2txt.process(temp_docx.name)
          elif uploaded_file.type == "text/plain":
              # For TXT files
              stringio = StringIO(uploaded_file.getvalue().decode("utf-8"))
              text_content = stringio.read()
          else:
              st.error(f"Unsupported file type: {uploaded_file.type}")
              continue
      except Exception as e:
          st.error(f"Error reading {file_name}: {e}")
          any_errors = True
          continue

      # Use OpenAI API to parse the content
      max_retries = 3
      retry_delay = 5  

      for attempt in range(max_retries):
          try:
              full_text_content = text_content

              # Update the prompt to use full_text_content
              prompt = f"""
              You will be provided data from the text. You must output them in a JSON format with the following keys:
              
              - questionStem
              - correctAnswerId
              - answersArray (as a list)
              - explanationList (as a list)
              - conditionName
              - moduleId
              - leadQuestion
              - presentationId
              - presentationId2
              
              You will categorise each question via the module they come under (the ID number for each question will be provided for you)
              You will categorise the article via presentation in which the number will already be provided to you.
              You will also provide the medical condition name that the question is related to in the conditionName field.
              
              For example:
              
              Question Stem:
              A 70-year-old man with a history of hypertension presents with sudden onset of severe chest pain radiating to the back.
              
              Lead Question:
              What is the most likely diagnosis?
              
              Correct Answer ID:
              3
              
              Answers Array:
              
              Aortic dissection
              Myocardial infarction
              Pulmonary embolism
              Pericarditis
              Pneumothorax
              
              Explanation List:
              
              Aortic dissection is most likely given the description of severe chest pain radiating to the back, a hallmark of this condition.
              Myocardial infarction can also cause chest pain, but it typically radiates to the arm or jaw rather than the back.
              Pulmonary embolism may cause chest pain but is usually associated with shortness of breath.
              Pericarditis often causes sharp chest pain that worsens with inspiration, but does not typically radiate to the back.
              Pneumothorax can cause chest pain, but it typically presents with sudden shortness of breath and is less likely to radiate to the back.
              
              conditionName: 7
              Module ID: 1
              Presentation ID 1: 41
              Presentation ID 2: 23
              
              You will then convert this into JSON format:
              {{
                "0": {{
                  "questionStem": "A 70-year-old man with a history of hypertension presents with sudden onset of severe chest pain radiating to the back.",
                  "correctAnswerId": "0",
                  "answersArray": [
                    "A. Aortic dissection",
                    "B. Myocardial infarction",
                    "C. Pulmonary embolism",
                    "D. Pericarditis",
                    "E. Pneumothorax"
                  ],
                  "explanationList": [
                    "A. Aortic dissection - Correct Answer: Aortic dissection is most likely given the description of severe chest pain radiating to the back, a hallmark of this condition.",
                    "B. Myocardial infarction - Incorrect Answer: Myocardial infarction can also cause chest pain, but it typically radiates to the arm or jaw rather than the back.",
                    "C. Pulmonary embolism - Incorrect Answer: Pulmonary embolism may cause chest pain but is usually associated with shortness of breath.",
                    "D. Pericarditis - Incorrect Answer: Pericarditis often causes sharp chest pain that worsens with inspiration, but does not typically radiate to the back.",
                    "E. Pneumothorax - Incorrect Answer: Pneumothorax can cause chest pain, but it typically presents with sudden shortness of breath and is less likely to radiate to the back."
                  ],
                  "conditionName": 7,
                  "moduleId": 1,
                  "presentationId": 41,
                  "presentationId2": 23,
                  "leadQuestion": "What is the most likely diagnosis?"
                }}
              }}
              
              Now, parse the following text and provide the output in the same JSON format, make sure that the moduleId is always an integer. YOU MUST ENSURE THAT THE LEAD-IN QUESTION IS ALWAYS SEPARATED FROM THE QUESTION STEM AND NEVER INCLUDED IN THE QUESTION STEM. 
              YOU MUST PARSE ALL questions in the text, not just the first one.
              You MUST ENSURE ALL ASPECTS OF THE EXPLANATION ARE INCLUDED E.G. SUPPORTED BY QUOTES AND WHY AN ANSWER IS RIGHT / WRONG IN A SPECIFIC CONTEXT. THE WHOLE PARAGRAPH MUST BE INCLUDED
              You MUST ENSURE EVERY SINGLE PEICE OF INFORMATION AND EVERY SINGLE WORD IS RETAINED. YOU ARE A PARSER SO YOU MUST INLCUDE EVERYTHING PROVIDED IN THE DOCUMENT. NEVER SUMMARISE. YOU MUST INCLUDE EVERY PARAGRAPH ON THE DOCUMENT
              {full_text_content}
              """

              response = client.chat.completions.create(
                  model="gpt-4o", 
                  messages=[
                      {"role": "system", "content": "You are a helpful assistant that extracts information from text and formats it as JSON."},
                      {"role": "user", "content": prompt}
                  ],
                  temperature=0,
                  max_tokens=None 
              )
              
              # Parse the JSON output from OpenAI
              json_response = response.choices[0].message.content.strip()
              
              # Remove the ```json prefix and suffix if present
              json_response = json_response.replace("```json", "").replace("```", "").strip()
              
              # Parse JSON
              parsed_data = json.loads(json_response)
              if isinstance(parsed_data, dict):
                  for key, question_set in parsed_data.items():
                      data_list.append(question_set)
              else:
                  data_list.append(parsed_data)
              st.success(f"Successfully parsed **{file_name}**.")
              break  
          
          except json.JSONDecodeError as json_error:
              if attempt < max_retries - 1:
                  st.warning(f"Error parsing JSON for {file_name}. Retrying in {retry_delay} seconds...")
                  time.sleep(retry_delay)
              else:
                  st.error(f"Error parsing JSON for {file_name} after {max_retries} attempts: {json_error}")
                  st.error(f"Raw response: {json_response}")
                  any_errors = True
          
          except Exception as e:
              st.error(f"Error processing {file_name}: {e}")
              any_errors = True
              break  

  if data_list:
      st.write("### Parsed Data:")
      st.json(data_list)

      # Confirm before uploading
      if st.button("Upload Data to Supabase"):
          st.write("Uploading data to Supabase...")
          upload_errors = False
          for record in data_list:
              try:
                  response = supabase.table("mcqQuestions").upsert(
                      record, 
                      on_conflict="questionStem"
                  ).execute()
                  
                  # Check if the operation was successful
                  if response.data is not None or len(response.data) > 0:
                      st.success(f"Successfully upserted record to mcqQuestions table.")
                  elif hasattr(response, 'error') and response.error:
                      st.error(f"Error uploading record: {response.error}")
                      upload_errors = True
                  else:
                      st.info(f"Record processed. No data returned (this is normal for upsert operations).")
              except Exception as e:
                  st.error(f"Exception during upload: {e}")
                  upload_errors = True
          if not upload_errors:
              st.success("All data processed successfully.")
          else:
              st.warning("Some data may have failed to upload. Please check the messages above.")
  else:
      if any_errors:
          st.warning("No data to upload due to errors in processing files.")
      else:
          st.warning("No data was parsed from the uploaded files.")
else:
  st.write("No files uploaded.")
