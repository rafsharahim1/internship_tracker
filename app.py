import streamlit as st  
import pandas as pd
import firebase_admin
from firebase_admin import credentials, auth, firestore, exceptions
from datetime import datetime, date
import requests

# Initialize Firebase Admin SDK
if not firebase_admin._apps:
    try:
        firebase_config = dict(st.secrets["firebase"])
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"Firebase initialization failed: {str(e)}")
        st.stop()

db = firestore.client()

# ----------------------
# Authentication Functions
# ----------------------
def is_iba_user(email):
    allowed_domains = ("@iba.edu.pk", "@khi.iba.edu.pk")
    return any(email.endswith(domain) for domain in allowed_domains)

def handle_auth_error(e):
    error_messages = {
        "EMAIL_NOT_FOUND": "Account not found",
        "INVALID_PASSWORD": "Invalid password",
        "USER_DISABLED": "Account disabled",
        "EMAIL_EXISTS": "Email already registered"
    }
    if hasattr(e, "code"):
        return error_messages.get(e.code, f"Authentication error: {str(e)}")
    else:
        return f"Authentication error: {str(e)}"

def sign_in_with_email_and_password(email, password):
    api_key = st.secrets["firebase"]["apiKey"]
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        return response.json()  # Contains "localId", "idToken", etc.
    else:
        error = response.json().get("error", {}).get("message", "Unknown error")
        raise Exception(error)

def send_password_reset_email(email):
    """Sends a password reset email via Firebase."""
    api_key = st.secrets["firebase"]["apiKey"]
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}"
    payload = {"requestType": "PASSWORD_RESET", "email": email}
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        return True
    else:
        error = response.json().get("error", {}).get("message", "Unknown error")
        raise Exception(error)

# ----------------------
# Session State Management
# ----------------------
if 'firebase_user' not in st.session_state:
    st.session_state.update({
        'firebase_user': None,
        'applications': pd.DataFrame(),
        'contributions': pd.DataFrame(),
        'bookmarks': [],
        'reviews': [],
        'show_form': False,
        'edit_review_index': None,
        'data_loaded': False,
        'page': "👤 User Profile",
        'dummy': False,
        'show_forgot': False  # flag for showing forgot password form
    })

# Read query parameters (read-only)
query_params = st.query_params
if "page" in query_params:
    st.session_state.page = query_params["page"][0]

# ----------------------
# Authentication Interface
# ----------------------
if not st.session_state.firebase_user:
    st.title("IBA Internship Portal")
    login_tab, register_tab = st.tabs(["Login", "Register"])
    with login_tab:
        with st.form("login_form"):
            email = st.text_input("IBA Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In")
            if submitted:
                try:
                    if not is_iba_user(email):
                        st.error("Only IBA email addresses allowed")
                    else:
                        user_info = sign_in_with_email_and_password(email, password)
                        st.session_state.firebase_user = user_info  # localId acts as UID
                        st.query_params = {"page": st.session_state.page}
                        st.stop()
                except Exception as e:
                    st.error(f"Authentication failed: {str(e)}")
        # Forgot Password link
        if st.button("Forgot Password?"):
            st.session_state.show_forgot = True

        # Forgot Password form
        if st.session_state.show_forgot:
            with st.form("forgot_form"):
                forgot_email = st.text_input("Enter your IBA Email for password reset")
                if st.form_submit_button("Send Reset Email"):
                    try:
                        if not is_iba_user(forgot_email):
                            st.error("Only IBA email addresses allowed")
                        else:
                            send_password_reset_email(forgot_email)
                            st.success("Password reset email sent!")
                            st.session_state.show_forgot = False
                    except Exception as e:
                        st.error(f"Failed to send reset email: {str(e)}")

    with register_tab:
        with st.form("register_form"):
            new_email = st.text_input("New IBA Email")
            new_password = st.text_input("New Password", type="password")
            if st.form_submit_button("Create Account"):
                if is_iba_user(new_email):
                    try:
                        user = auth.create_user(
                            email=new_email,
                            password=new_password,
                            email_verified=False
                        )
                        link = auth.generate_email_verification_link(new_email)
                        st.success("Account created! Check your email for verification")
                    except Exception as e:
                        st.error(handle_auth_error(e))
                else:
                    st.error("Only IBA email addresses allowed")
    st.stop()

# ----------------------
# Ensure User Profile is Completed
# ----------------------
def complete_profile():
    st.header("Complete Your Profile")
    with st.form("profile_form"):
        full_name = st.text_input("Full Name")
        age = st.number_input("Age", min_value=16, max_value=100, step=1)
        semester = st.number_input("Current Semester", min_value=1, max_value=12, step=1)
        program = st.text_input("Program")
        grad_year = st.number_input("Expected Graduation Year", min_value=2023, max_value=2100, step=1)
        submitted = st.form_submit_button("Save Profile")
        if submitted:
            profile_data = {
                "full_name": full_name,
                "age": age,
                "semester": semester,
                "program": program,
                "expected_grad_year": grad_year,
                "profile_completed": True
            }
            try:
                user_ref = db.collection("users").document(st.session_state.firebase_user["localId"])
                user_ref.set(profile_data, merge=True)
                st.success("Profile saved!")
                st.experimental_rerun()  # Refresh to show dashboard with profile info
            except Exception as e:
                st.error(f"Failed to save profile: {str(e)}")

# Check if profile exists and is complete
user_ref = db.collection("users").document(st.session_state.firebase_user["localId"])
user_doc = user_ref.get()
profile_completed = False
if user_doc.exists:
    user_profile_data = user_doc.to_dict()
    profile_completed = user_profile_data.get("profile_completed", False)
else:
    user_profile_data = {}

if not profile_completed:
    complete_profile()
    st.stop()

# ----------------------
# Data Management Functions
# ----------------------
def load_data():
    try:
        user_ref = db.collection("users").document(st.session_state.firebase_user["localId"])
        apps_ref = user_ref.collection("applications")
        apps = [doc.to_dict() for doc in apps_ref.stream()]
        st.session_state.applications = pd.DataFrame(apps) if apps else pd.DataFrame()
        user_data = user_ref.get().to_dict() or {}
        st.session_state.contributions = pd.DataFrame(user_data.get("contributions", []))
        st.session_state.bookmarks = user_data.get("bookmarks", [])
        reviews_ref = db.collection("reviews")
        st.session_state.reviews = [{**doc.to_dict(), "id": doc.id} for doc in reviews_ref.stream()]
    except Exception as e:
        st.error(f"Data load failed: {str(e)}")

if not st.session_state.data_loaded:
    load_data()
    st.session_state.data_loaded = True

def save_applications():
    try:
        apps_ref = db.collection("users").document(st.session_state.firebase_user["localId"]).collection("applications")
        for doc in apps_ref.stream():
            doc.reference.delete()
        for _, row in st.session_state.applications.iterrows():
            row_dict = row.to_dict()
            if "Deadline" in row_dict:
                if isinstance(row_dict["Deadline"], date) and not isinstance(row_dict["Deadline"], datetime):
                    row_dict["Deadline"] = datetime.combine(row_dict["Deadline"], datetime.min.time())
            apps_ref.add(row_dict)
    except Exception as e:
        st.error(f"Failed to save applications: {str(e)}")

def save_contributions():
    try:
        user_ref = db.collection("users").document(st.session_state.firebase_user["localId"])
        user_ref.update({"contributions": st.session_state.contributions.to_dict("records")})
    except Exception as e:
        st.error(f"Failed to save contributions: {str(e)}")

def save_bookmarks():
    try:
        user_ref = db.collection("users").document(st.session_state.firebase_user["localId"])
        user_ref.update({"bookmarks": list(set(st.session_state.bookmarks))})
    except Exception as e:
        st.error(f"Failed to save bookmarks: {str(e)}")

def save_review(review_data):
    try:
        reviews_ref = db.collection("reviews")
        if st.session_state.edit_review_index is not None:
            doc_id = st.session_state.reviews[st.session_state.edit_review_index]['id']
            reviews_ref.document(doc_id).update(review_data)
        else:
            review_data['upvoters'] = []
            review_data['bookmarkers'] = []
            new_doc = reviews_ref.add(review_data)
            review_data['id'] = new_doc[1].id
        load_data()  # Refresh data after save
    except Exception as e:
        st.error(f"Failed to save review: {str(e)}")

# ----------------------
# Helper Functions
# ----------------------
def calculate_kpis():
    if st.session_state.applications.empty:
        return {'Total Applications': 0, 'Rejected': 0, 'In Progress': 0}
    if 'Status' not in st.session_state.applications.columns:
        total = len(st.session_state.applications)
        return {'Total Applications': total, 'Rejected': 0, 'In Progress': total}
    total = len(st.session_state.applications)
    rejected = len(st.session_state.applications[st.session_state.applications['Status'] == 'Rejected'])
    in_progress = len(st.session_state.applications[~st.session_state.applications['Status'].isin(['Offer Received', 'Rejected'])])
    return {'Total Applications': total, 'Rejected': rejected, 'In Progress': in_progress}

def validate_stipend(stipend):
    if not stipend:
        return True
    try:
        parts = stipend.split('-')
        return len(parts) == 2 and all(part.strip().isdigit() for part in parts)
    except:
        return False

# ----------------------
# Sidebar Navigation and Page Storage
# ----------------------
if "page" not in st.session_state:
    st.session_state.page = "👤 User Profile"

page = st.sidebar.radio("Go to", ("👤 User Profile", "📰 Internship Feed"),
                          index=0 if st.session_state.get("page", "👤 User Profile") == "👤 User Profile" else 1)
st.session_state.page = page

# ----------------------
# User Profile Page
# ----------------------
def user_profile():
    # Display the basic profile info at the top
    st.subheader("Your Profile Information")
    st.write(f"**Name:** {user_profile_data.get('full_name', 'N/A')}")
    st.write(f"**Age:** {user_profile_data.get('age', 'N/A')}")
    st.write(f"**Semester:** {user_profile_data.get('semester', 'N/A')}")
    st.write(f"**Program:** {user_profile_data.get('program', 'N/A')}")
    st.write(f"**Expected Graduation:** {user_profile_data.get('expected_grad_year', 'N/A')}")
    
    # KPIs and rest of the dashboard below
    st.title('User Job Application Dashboard')
    kpis = calculate_kpis()
    cols = st.columns(3)
    cols[0].metric("Applications", kpis['Total Applications'])
    cols[1].metric("Rejected", kpis['Rejected'])
    cols[2].metric("In Progress", kpis['In Progress'])
    
    st.header("Applications Tracker")
    with st.expander("➕ Add New Application"):
        with st.form("new_application"):
            name = st.text_input("Company Name")
            status = st.selectbox("Status", ['Applied', 'Assessment Given', 'Interview R1 given',
                                               'Interview R2 given', 'Interview R3 given', 
                                               'Accepted', 'Offer Received', 'Rejected'])
            deadline = st.date_input("Deadline")
            referral = st.text_input("Referral Details")
            link = st.text_input("Application Link")
            notes = st.text_area("Notes")
            if st.form_submit_button("Add Application"):
                deadline_dt = datetime.combine(deadline, datetime.min.time())
                new_app = pd.DataFrame([{'Company Name': name,
                                          'Status': status,
                                          'Deadline': deadline_dt,
                                          'Referral Details': referral,
                                          'Link': link,
                                          'Notes': notes}])
                st.session_state.applications = pd.concat([st.session_state.applications, new_app], ignore_index=True)
                save_applications()
                st.stop()
    
    edited_df = st.data_editor(st.session_state.applications,
                               column_config={"Deadline": st.column_config.DateColumn(),
                                              "Link": st.column_config.LinkColumn()},
                               num_rows="dynamic")
    if not edited_df.equals(st.session_state.applications):
        st.session_state.applications = edited_df
        save_applications()
    
    
    
    # Display Bookmarked Reviews
    current_user = st.session_state.firebase_user["localId"]
    bookmarked_reviews = [review for review in st.session_state.reviews if current_user in review.get("bookmarkers", [])]
    st.header("Bookmarked Reviews")
    if bookmarked_reviews:
        for review in bookmarked_reviews:
            st.markdown(f"### {review['Company']} ({review['Industry']})")
            st.caption(f"👨💻 {review['Department']} | 🎓 Semester {review['Semester']}")
            st.write(f"**Process:** {review['Ease of Process']}")
            st.write(f"**Outcome:** {review['Offer Outcome']}")
            st.write(f"**Upvotes:** {len(review.get('upvoters', []))}  |  **Bookmarks:** {len(review.get('bookmarkers', []))}")
    else:
        st.write("No bookmarked reviews.")
    
    # Display Your Reviews (submitted reviews) with Edit Option
    st.header("Your Reviews")
    user_reviews = [(i, review) for i, review in enumerate(st.session_state.reviews)
                    if review.get("user_id") == st.session_state.firebase_user["localId"]]
    if user_reviews:
        for i, review in user_reviews:
            col1, col2 = st.columns([8,2])
            reviewer_display = review.get("reviewer_name", "Anonymous")
            col1.markdown(f"**{review['Company']} ({review['Industry']})** - {review['Offer Outcome']}")
            col1.caption(f"Reviewed by: {reviewer_display}")
            if col2.button("Edit", key=f"edit_{i}"):
                st.session_state.edit_review_index = i
                st.session_state.page = "📰 Internship Feed"
                st.query_params = {"page": "📰 Internship Feed"}
                st.stop()
    else:
        st.write("You have not submitted any reviews yet.")

# ----------------------
# Internship Feed Page
# ----------------------
def internship_feed():
    st.header("🎯 Internship Feed")
    col1, col2, col3, col4 = st.columns([2,2,2,1])
    company_search = col1.text_input("Search by Company")
    industry_filter = col2.selectbox("Industry", ["All", "Tech", "Finance", "Marketing", "HR"])
    stipend_range = col3.slider("Stipend Range (₹)", 0, 150000, (30000, 100000))
    
    if col4.button("➕ Add Review"):
        st.session_state.show_form = True
        st.session_state.edit_review_index = None
    
    review_to_edit = None
    if st.session_state.edit_review_index is not None:
        review_to_edit = st.session_state.reviews[st.session_state.edit_review_index]
    
    if st.session_state.show_form:
        with st.form("review_form", clear_on_submit=True):
            # Option to post with full name or anonymously
            post_option = st.radio("How would you like to post your review?",
                                     ["Use my full name", "Post Anonymously"],
                                     index=0)
            col1, col2 = st.columns(2)
            with col1:
                company_options = ['Google', 'Microsoft', 'Goldman Sachs', 'Other']
                default_company = review_to_edit['Company'] if review_to_edit else 'Google'
                if default_company in company_options:
                    company = st.selectbox("Company", company_options, index=company_options.index(default_company))
                else:
                    company = st.selectbox("Company", company_options, index=company_options.index('Other'))
                custom_company = st.text_input("Custom Company", value=review_to_edit.get("Custom Company", "") if review_to_edit and company=='Other' else "")
                industry_options = ["Tech", "Finance", "Marketing", "HR", "Other"]
                default_industry = review_to_edit['Industry'] if review_to_edit else "Tech"
                industry = st.selectbox("Industry", industry_options, index=industry_options.index(default_industry))
                ease_process_options = ["Easy", "Moderate", "Hard"]
                default_ease = review_to_edit['Ease of Process'] if review_to_edit else "Easy"
                ease_process = st.selectbox("Ease of Process", ease_process_options, index=ease_process_options.index(default_ease))
                assessments = st.text_area("Gamified Assessments", value=review_to_edit.get("Gamified Assessments", "") if review_to_edit else "")
                interview_questions = st.text_area("Interview Questions", value=review_to_edit.get("Interview Questions", "") if review_to_edit else "")
                stipend = st.text_input("Stipend Range (₹) (Optional)", value=review_to_edit.get("Stipend Range", "") if review_to_edit else "")
            with col2:
                hiring_rating = st.slider("Hiring Ease (1-5)", 1, 5, value=review_to_edit.get("Ease of Hiring", 3) if review_to_edit else 3)
                referral = st.radio("Referral Used?", ["Yes", "No"], index=["Yes", "No"].index(review_to_edit.get("Referral Used", "Yes")) if review_to_edit else 0)
                red_flags = st.slider("Red Flags (1-5)", 1, 5, value=review_to_edit.get("Red Flags", 3) if review_to_edit else 3)
                department_options = ["Tech", "Finance", "HR", "Marketing", "Operations"]
                default_dept = review_to_edit['Department'] if review_to_edit else "Tech"
                department = st.selectbox("Department", department_options, index=department_options.index(default_dept))
                semester = st.slider("Semester", 1, 8, value=review_to_edit.get("Semester", 5) if review_to_edit else 5)
                outcome_options = ["Accepted", "Rejected", "In Process"]
                default_outcome = review_to_edit['Offer Outcome'] if review_to_edit else "Accepted"
                outcome = st.selectbox("Outcome", outcome_options, index=outcome_options.index(default_outcome))
            if st.form_submit_button("Submit Review"):
                errors = []
                if company == 'Other' and not custom_company:
                    errors.append("Company name required")
                if not validate_stipend(stipend):
                    errors.append("Invalid stipend format (use 'min-max' or leave empty)")
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    reviewer_name = user_profile_data.get("full_name", "Anonymous") if post_option == "Use my full name" else "Anonymous"
                    new_review = {
                        'user_id': st.session_state.firebase_user["localId"],
                        'Company': custom_company if company == 'Other' else company,
                        'Industry': industry,
                        'Ease of Process': ease_process,
                        'Gamified Assessments': assessments,
                        'Interview Questions': interview_questions,
                        'Stipend Range': stipend if stipend else "Not Specified",
                        'Ease of Hiring': hiring_rating,
                        'Referral Used': referral,
                        'Red Flags': red_flags,
                        'Department': department,
                        'Semester': semester,
                        'Offer Outcome': outcome,
                        'reviewer_name': reviewer_name,
                        'upvoters': review_to_edit.get("upvoters", []) if review_to_edit else [],
                        'bookmarkers': review_to_edit.get("bookmarkers", []) if review_to_edit else [],
                        'timestamp': firestore.SERVER_TIMESTAMP
                    }
                    save_review(new_review)
                    st.success("Review Submitted!")
                    # Close review form after submission
                    st.session_state.show_form = False
                    st.session_state.edit_review_index = None
                    st.session_state.page = "📰 Internship Feed"
                    st.query_params = {"page": "📰 Internship Feed"}
                    #st.experimental_rerun()
                    st.session_state.dummy = not st.session_state.get("dummy", False)
                    #st.stop()
    
    filtered_reviews = []
    for review in st.session_state.reviews:
        try:
            stipend_val = review.get('Stipend Range', '0-0')
            min_stipend = max_stipend = 0
            if stipend_val != "Not Specified":
                parts = stipend_val.split('-')
                min_stipend, max_stipend = int(parts[0].strip()), int(parts[1].strip())
            matches = (
                (company_search.lower() in review['Company'].lower()) and
                (industry_filter == "All" or review['Industry'] == industry_filter) and
                (min_stipend >= stipend_range[0]) and 
                (max_stipend <= stipend_range[1])
            )
            if matches:
                filtered_reviews.append(review)
        except:
            continue
    
    st.subheader("Top Reviews")
    for idx, review in enumerate(sorted(filtered_reviews, key=lambda x: len(x.get("upvoters", [])), reverse=True)[:5]):
        with st.container(border=True):
            col1, col2 = st.columns([4,1])
            with col1:
                st.markdown(f"### {review['Company']} ({review['Industry']})")
                st.caption(f"👨💻 {review['Department']} | 🎓 Semester {review['Semester']}")
                st.write(f"**Process:** {review['Ease of Process']}")
                st.write(f"**Stipend:** {review['Stipend Range']}")
                st.write(f"**Rating:** {'⭐' * review['Ease of Hiring']}")
                st.write(f"**Red Flags:** {'🚩' * review['Red Flags']}")
                with st.expander("Details"):
                    st.write(f"**Assessments:** {review['Gamified Assessments']}")
                    st.write(f"**Questions:** {review['Interview Questions']}")
            with col2:
                st.write(f"**Outcome:** {review['Offer Outcome']}")
                user_id = st.session_state.firebase_user["localId"]
                upvoters = review.get("upvoters", [])
                bookmarkers = review.get("bookmarkers", [])
                if user_id in upvoters:
                    if st.button(f"Remove Upvote (👍 {len(upvoters)})", key=f"upvote_{idx}"):
                        review_ref = db.collection("reviews").document(review['id'])
                        review_ref.update({"upvoters": firestore.ArrayRemove([user_id])})
                        load_data()
                else:
                    if st.button(f"Upvote (👍 {len(upvoters)})", key=f"upvote_{idx}"):
                        review_ref = db.collection("reviews").document(review['id'])
                        review_ref.update({"upvoters": firestore.ArrayUnion([user_id])})
                        load_data()
                if user_id in bookmarkers:
                    if st.button(f"Remove Bookmark (🔖 {len(bookmarkers)})", key=f"bookmark_{idx}"):
                        review_ref = db.collection("reviews").document(review['id'])
                        review_ref.update({"bookmarkers": firestore.ArrayRemove([user_id])})
                        load_data()
                else:
                    if st.button(f"Bookmark (🔖 {len(bookmarkers)})", key=f"bookmark_{idx}"):
                        review_ref = db.collection("reviews").document(review['id'])
                        review_ref.update({"bookmarkers": firestore.ArrayUnion([user_id])})
                        load_data()

if st.session_state.page == "👤 User Profile":
    user_profile()
else:
    internship_feed()

if st.session_state.firebase_user:
    if st.sidebar.button("Logout"):
        st.session_state.clear()
        st.query_params = {}
        st.stop()

st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 18px; }
    [data-testid="stMetricLabel"] { font-size: 16px; }
    .stDataFrame { margin-bottom: 20px; }
    [data-testid="stExpander"] div[role="button"] p { font-size: 1.2rem; font-weight: bold; }
    .stButton>button { width: 100%; margin: 5px 0; transition: all 0.3s ease; }
    .stButton>button:hover { transform: scale(1.05); box-shadow: 0 2px 5px rgba(0,0,0,0.2); }
    .stContainer { border-radius: 10px; padding: 20px; margin: 10px 0; box-shadow: 0 2px 5px rgba(0,0,0,0.1); background: #f8f9fa; }
</style>
""", unsafe_allow_html=True)
