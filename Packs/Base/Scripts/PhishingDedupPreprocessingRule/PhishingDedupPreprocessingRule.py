import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *
import pandas as pd
# from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from scipy import spatial
from numpy import dot
from numpy.linalg import norm

SIMILARITY_THRESHOLD = 0.98

EMAIL_BODY_FIELD = 'emailbody'
EMAIL_SUBJECT_FIELD = 'emailsubject'
EMAIL_HTML_FIELD = 'emailbodyhtml'
MERGED_TEXT_FIELD = 'mereged_text'
MIN_TEXT_LENGTH = 50

def get_existing_incidents(input_args):
    get_incidents_args = {}
    for arg in ['incidentTypes', 'query', 'limit']:
        if arg in input_args:
            get_incidents_args[arg] = input_args[arg]
    if 'exsitingIncidentsLookback' in input_args:
        get_incidents_args['fromDate'] = input_args['exsitingIncidentsLookback']
    incidents_query_res = demisto.executeCommand('GetIncidentsByQuery', get_incidents_args)
    if is_error(incidents_query_res):
        return_error(get_error(incidents_query_res))
    incidents = json.loads(incidents_query_res[-1]['Contents'])
    return incidents


def get_text_from_html(html):
    return html
    # todo: change to docker which supports
    soup = BeautifulSoup(html)
    # kill all script and style elements
    for script in soup(["script", "style"]):
        script.extract()  # rip it out
    # get text
    text = soup.get_text()
    # break into lines and remove leading and trailing space on each
    lines = (line.strip() for line in text.splitlines())
    # break multi-headlines into a line each
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    # drop blank lines
    text = '\n'.join(chunk for chunk in chunks if chunk)
    return text

def preprocess_text_fields(incident):
    email_body = email_subject = email_html = ''
    if EMAIL_BODY_FIELD in incident:
        email_body = incident[EMAIL_BODY_FIELD]
    if "CustomFields" in incident and EMAIL_BODY_FIELD in incident["CustomFields"]:
        email_body = incident["CustomFields"][EMAIL_BODY_FIELD]

    if EMAIL_SUBJECT_FIELD in incident:
        email_subject = incident[EMAIL_SUBJECT_FIELD]
    if "CustomFields" in incident and EMAIL_SUBJECT_FIELD in incident["CustomFields"]:
        email_subject = incident["CustomFields"][EMAIL_SUBJECT_FIELD]

    if EMAIL_HTML_FIELD in incident:
        email_html = incident[EMAIL_HTML_FIELD]
    if "CustomFields" in incident and EMAIL_HTML_FIELD in incident["CustomFields"]:
        email_html = incident["CustomFields"][EMAIL_HTML_FIELD]

    if isinstance(email_html, float):
        email_html = ''
    if email_body is None or isinstance(email_body, float) or email_body.strip() == '':
        email_body = get_text_from_html(email_html)
    if isinstance(email_subject, float):
        email_subject = ''
    return email_subject + ' ' + email_body

def preprocess_existing_incidents(existing_incidents, new_incident):
    global MERGED_TEXT_FIELD
    existing_incidents_df = pd.DataFrame(existing_incidents)
    same_id_mask = existing_incidents_df['id'] == new_incident['id']
    existing_incidents_df = existing_incidents_df[~same_id_mask]
    existing_incidents_df[MERGED_TEXT_FIELD] = existing_incidents_df.apply(lambda x: preprocess_text_fields(x), axis=1)
    existing_incidents_df = existing_incidents_df[existing_incidents_df[MERGED_TEXT_FIELD].str.len() >= MIN_TEXT_LENGTH]
    existing_incidents_df.reset_index(inplace=True)
    return existing_incidents_df

def vectorize(text, vectorizer):
    return vectorizer.transform([text]).toarray()[0]


def cosine_sim(a, b):
    return dot(a, b)/(norm(a)*norm(b))


def find_duplicate_incidents(new_incident_text, existing_incidents_df):
    global MERGED_TEXT_FIELD
    text = [new_incident_text] + existing_incidents_df[MERGED_TEXT_FIELD].tolist()
    vectorizer = TfidfVectorizer(token_pattern=r"(?u)\b\w\w+\b|!|\?|\"|\'").fit(text)
    new_incident_vector = vectorize(new_incident_text, vectorizer)
    existing_incidents_df['vector'] = existing_incidents_df[MERGED_TEXT_FIELD].apply(lambda x: vectorize(x,vectorizer))
    existing_incidents_df['similarity'] = existing_incidents_df['vector'].apply(lambda x: cosine_sim(x, new_incident_vector))
    existing_incidents_df.sort_values(by='similarity', ascending=False, inplace=True)
    if len(existing_incidents_df) > 0:
        return existing_incidents_df.iloc[0], existing_incidents_df.iloc[0]['similarity']
    else:
        return None, None


def close_new_incident_and_link_to_existing(new_incident, existing_incident, similarity):
    entries = []
    entries.append({'Contents': "Duplicate incident: " + new_incident['name']})
    entries.append({"Type": entryTypes['note'], "ContentsFormat": "json", "Contents": json.dumps(new_incident)})
    entries_str = json.dumps(entries)
    demisto.executeCommand("addEntries", {"id": existing_incident["id"], "entries": entries_str})
    demisto.results(False)
    return 'most similar message: {} {}'.format(similarity, existing_incident[MERGED_TEXT_FIELD])


def create_new_incident():
    demisto.results(True)



def main():
    global EMAIL_BODY_FIELD, EMAIL_SUBJECT_FIELD, EMAIL_HTML_FIELD, MIN_TEXT_LENGTH
    input_args = demisto.args()
    EMAIL_BODY_FIELD = input_args.get('emailBody', EMAIL_BODY_FIELD)
    EMAIL_SUBJECT_FIELD = input_args.get('emailSubject', EMAIL_SUBJECT_FIELD)
    EMAIL_HTML_FIELD = input_args.get('emailBodyHTML', EMAIL_HTML_FIELD)
    existing_incidents = get_existing_incidents(input_args)
    if len(existing_incidents) == 0:
        create_new_incident()
        return
    new_incident = demisto.incidents()[0]
    existing_incidents_df = preprocess_existing_incidents(existing_incidents, new_incident)
    if len(existing_incidents_df) == 0:
        create_new_incident()
        return
    new_incident_text = preprocess_text_fields(new_incident)
    if len(new_incident_text) < MIN_TEXT_LENGTH:
        create_new_incident()
        return
    duplicate_incident_row, similarity = find_duplicate_incidents(new_incident_text, existing_incidents_df)
    if duplicate_incident_row is None or similarity < SIMILARITY_THRESHOLD:
        create_new_incident()
    else:
        return close_new_incident_and_link_to_existing(new_incident, duplicate_incident_row, similarity)


if __name__ in ['__main__', '__builtin__', 'builtins']:
    main()
