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
              - moduleId
              - leadQuestion
              
              You will choose the module id from the list
              
              1 - Acute and emergency
              2 - Cancer
              3 - Cardiovascular
              4 - Child health
              5 - Clinical Haematology
              6 - Clinical imaging
              7 - Dermatology
              8 - Ear, nose and throat
              9 - Endocrine and metabolic
              10 - Gastrointestinal including liver
              11 - General practice and primary healthcare
              12 - Infection
              13 - Mental health
              14 - Musculoskeletal
              15 - Neurosciences
              16 - Obstetrics and gynaecology
              17 - Ophthalmo  logy
              18 - Perioperative medicine and anaesthesia
              19 - Renal and urology
              20 - Respiratory
              21 - Surgery
              22 - Allergy and immunology
              23 - Clinical biochemistry
              24 - Clinical pharmacology and therapeutics
              25 - Genetics and genomics
              26 - Laboratory haematology
              27 - Palliative and end of life care
              28 - Social and population health
              
              You will also categorise the article via presentation:
               1. Abdominal distension  
               2. Abdominal mass  
               3. Abnormal cervical smear result  
               4. Abnormal development/developmental delay  
               5. Abnormal eating or exercising behavior  
               6. Abnormal involuntary movements  
               7. Abnormal urinalysis  
               8. Acute abdominal pain  
               9. Acute and chronic pain management  
              10. Acute change in or loss of vision  
              11. Acute joint pain/swelling  
              12. Acute kidney injury  
              13. Acute rash  
              14. Addiction  
              15. Allergies  
              16. Altered sensation, numbness and tingling  
              17. Amenorrhoea  
              18. Anaphylaxis  
              19. Anosmia  
              20. Anxiety, phobias, OCD  
              21. Ascites  
              22. Auditory hallucinations  
              23. Back pain  
              24. Behaviour/personality change  
              25. Behavioural difficulties in childhood  
              26. Bites and stings  
              27. Blackouts and faints  
              28. Bleeding antepartum  
              29. Bleeding from lower GI tract  
              30. Bleeding from upper GI tract  
              31. Bleeding postpartum  
              32. Bone pain  
              33. Breast lump  
              34. Breast tenderness/pain  
              35. Breathlessness  
              36. Bruising  
              37. Burns  
              38. Cardiorespiratory arrest  
              39. Change in bowel habit  
              40. Change in stool color  
              41. Chest pain  
              42. Child abuse  
              43. Chronic abdominal pain  
              44. Chronic joint pain/stiffness  
              45. Chronic kidney disease  
              46. Chronic rash  
              47. Cold, painful, pale, pulseless leg/foot  
              48. Complications of labour  
              49. Confusion  
              50. Congenital abnormalities  
              51. Constipation  
              52. Contraception request/advice  
              53. Cough  
              54. Crying baby  
              55. Cyanosis  
              56. Death and dying  
              57. Decreased appetite  
              58. Decreased/loss of consciousness  
              59. Dehydration  
              60. Deteriorating patient  
              61. Diarrhoea  
              62. Difficulty with breastfeeding  
              63. Diplopia  
              64. Dizziness  
              65. Driving advice  
              66. Dysmorphic child  
              67. Ear and nasal discharge  
              68. Elation/elated mood  
              69. Elder abuse  
              70. Electrolyte abnormalities  
              71. End of life care/symptoms of terminal illness  
              72. Epistaxis  
              73. Erectile dysfunction  
              74. Eye pain/discomfort  
              75. Eye trauma  
              76. Facial pain  
              77. Facial weakness  
              78. Facial/periorbital swelling  
              79. Faecal incontinence  
              80. Falls  
              81. Family history of possible genetic disorder  
              82. Fasciculation  
              83. Fatigue  
              84. Fever  
              85. Fit notes  
              86. Fits/seizures  
              87. Fixed abnormal beliefs  
              88. Flashes and floaters in visual fields  
              89. Food intolerance  
              90. Foreign body in eye  
              91. Frailty  
              92. Gradual change in or loss of vision  
              93. Gynaecomastia  
              94. Haematuria  
              95. Haemoptysis  
              96. Head injury  
              97. Headache  
              98. Hearing loss  
              99. Heart murmurs  
              100. Hoarseness and voice change  
              101. Hyperemesis  
              102. Hypertension  
              103. Immobility  
              104. Incidental findings  
              105. Infant feeding problems  
              106. Intrauterine death  
              107. Jaundice  
              108. Labour  
              109. Lacerations  
              110. Learning disability  
              111. Limb claudication  
              112. Limb weakness  
              113. Limp  
              114. Loin pain  
              115. Loss of libido  
              116. Loss of red reflex  
              117. Low blood pressure  
              118. Low mood/affective problems  
              119. Lump in groin  
              120. Lymphadenopathy  
              121. Massive haemorrhage  
              122. Melaena  
              123. Memory loss  
              124. Menopausal problems  
              125. Menstrual problems  
              126. Mental capacity concerns  
              127. Mental health problems in pregnancy or postpartum  
              128. Misplaced nasogastric tube  
              129. Muscle pain/myalgia  
              130. Musculoskeletal deformities  
              131. Nail abnormalities  
              132. Nasal obstruction  
              133. Nausea  
              134. Neck lump  
              135. Neck pain/stiffness  
              136. Neonatal death or cot death  
              137. Neuromuscular weakness  
              138. Night sweats  
              139. Nipple discharge  
              140. Normal pregnancy and antenatal care  
              141. Oliguria  
              142. Organomegaly  
              143. Overdose  
              144. Pain on inspiration  
              145. Painful ear  
              146. Painful sexual intercourse  
              147. Painful swollen leg  
              148. Pallor  
              149. Palpitations  
              150. Pelvic mass  
              151. Pelvic pain  
              152. Perianal symptoms  
              153. Peripheral oedema and ankle swelling  
              154. Petechial rash  
              155. Pleural effusion  
              156. Poisoning  
              157. Polydipsia (thirst)  
              158. Post-surgical care and complications  
              159. Pregnancy risk assessment  
              160. Prematurity  
              161. Pressure of speech  
              162. Pruritus  
              163. Ptosis  
              164. Pubertal development  
              165. Purpura  
              166. Rectal prolapse  
              167. Red eye  
              168. Reduced/change in fetal movements  
              169. Scarring  
              170. Scrotal/testicular pain and/or lump/swelling  
              171. Self-harm  
              172. Shock  
              173. Skin lesion  
              174. Skin or subcutaneous lump  
              175. Skin ulcers  
              176. Sleep problems  
              177. Small for gestational age/large for gestational age  
              178. Snoring  
              179. Soft tissue injury  
              180. Somatisation/medically unexplained physical symptoms  
              181. Sore throat  
              182. Speech and language problems  
              183. Squint  
              184. Stridor  
              185. Struggling to cope at home  
              186. Subfertility  
              187. Substance misuse  
              188. Suicidal thoughts  
              189. Swallowing problems  
              190. The sick child  
              191. Threats to harm others  
              192. Tinnitus  
              193. Trauma  
              194. Travel health advice  
              195. Tremor  
              196. Unsteadiness  
              197. Unwanted pregnancy and termination  
              198. Urethral discharge and genital ulcers/warts  
              199. Urinary incontinence  
              200. Urinary symptoms  
              201. Vaccination  
              202. Vaginal discharge  
              203. Vaginal prolapse  
              204. Vertigo  
              205. Visual hallucinations  
              206. Vomiting  
              207. Vulval itching/lesion  
              208. Vulval/vaginal lump  
              209. Weight gain  
              210. Weight loss  
              211. Wellbeing checks  
              212. Wheeze
              
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
              Module ID: 1
              Presentation ID: 41 
              
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
                  "moduleId": 1,
                  "presentationId: 41,
                  "leadQuestion": "What is the most likely diagnosis?"
                }}
              }}
              
              Now, parse the following text and provide the output in the same JSON format, make sure that the moduleId is always an integer. YOU MUST ENSURE THAT THE LEAD-IN QUESTION IS ALWAYS SEPARATED FROM THE QUESTION STEM AND NEVER INCLUDED IN THE QUESTION STEM. Parse ALL questions in the text, not just the first one:
              You MUST ENSURE EVERY SINGLE PEICE OF INFORMATION AND EVERY SINGLE WORD IS RETAINED. YOU ARE A PARSER SO YOU MUST INLCUDE EVERYTHING PROVIDED IN THE DOCUMENT.
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
