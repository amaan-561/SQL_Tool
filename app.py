import streamlit as st
import pandas as pd
import numpy as np
import datetime
import openpyxl
import xlrd
import io

# --- Helper Functions (Unchanged) ---

def escape_mysql(val):
    """
    Escapes a string for use in a MySQL query.
    """
    if not isinstance(val, str):
        val = str(val)
    # Order matters: \ must be escaped first
    val = val.replace('\\', '\\\\')
    val = val.replace('\'', '\\\'')
    val = val.replace('"', '\\"')
    val = val.replace('\n', '\\n')
    val = val.replace('\r', '\\r')
    return val

def format_sql_value(val):
    """
    Formats a Python value into its correct SQL string representation.
    """
    if pd.isnull(val):
        return 'NULL'
    # Check for date/datetime objects first
    if isinstance(val, (datetime.date, datetime.datetime)):
        return f"'{val.isoformat()}'"  # MySQL understands ISO format
    # Check for numeric types
    if isinstance(val, (int, float, np.integer, np.floating)):
        return str(val)
    # Check for boolean types
    if isinstance(val, (bool, np.bool_)):
        return 'TRUE' if val else 'FALSE'
    # Everything else is treated as a string and escaped
    return f"'{escape_mysql(val)}'"

def generate_chunked_insert_queries(df, table_name, chunk_size=1000, use_ignore=False):
    """
    Generates a series of chunked INSERT statements from a DataFrame.
    """
    queries = []
    columns = ', '.join([f"`{col}`" for col in df.columns])
    insert_command = "INSERT IGNORE INTO" if use_ignore else "INSERT INTO"
    
    for start in range(0, len(df), chunk_size):
        end = min(start + chunk_size, len(df))
        df_chunk = df.iloc[start:end]
        
        values_list = []
        for _, row in df_chunk.iterrows():
            values = ', '.join([format_sql_value(val) for val in row])
            values_list.append(f"({values})")
        
        if not values_list:
            continue  # Skip empty chunks
            
        all_values = ',\n  '.join(values_list)
        query = f"{insert_command} `{table_name}` ({columns}) VALUES\n  {all_values};"
        queries.append(query)
    
    return '\n\n-- -- -- -- -- -- -- -- -- --\n\n'.join(queries)

# --- Streamlit App ---

st.set_page_config(layout="wide")
st.title("📄 Excel/CSV to MySQL Batch Insert Generator")

uploaded_file = st.file_uploader("Upload your Excel or CSV file", type=["xls", "xlsx", "csv"])

if uploaded_file:
    df = None
    try:
        with st.spinner(f"Loading {uploaded_file.name}..."):
            file_type = uploaded_file.name.split('.')[-1]
            if file_type == 'csv':
                df = pd.read_csv(uploaded_file)
            elif file_type in ['xlsx','xls']:
                df = pd.read_excel(uploaded_file)
            else:
                st.error("Unsupported file type. Please upload a .csv, .xls, or .xlsx file.")
    except Exception as e:
        st.error(f"Error reading file: {e}")
        st.stop()

    if df is None:
        st.error("Failed to load data. The file might be empty or corrupted.")
        st.stop()

    if df.empty:
        st.warning("The uploaded file is empty.")
        st.stop()

    st.subheader("Preview of Uploaded Data (First 50 Rows)")
    st.dataframe(df.head(50))
    st.info(f"Total rows found: **{len(df)}**")

    st.divider()

    # --- ### MODIFIED SECTION ### ---
    
    new_column_names = {}
    with st.expander("📝 Rename Columns (Optional)"):
        st.caption("Change any column names to match your target database table.")
        
        # Create a header row
        header_cols = st.columns(2)
        header_cols[0].markdown("**Original Column**")
        header_cols[1].markdown("**New Name (editable)**")
        
        # Create a row for each column
        for col in df.columns:
            row_cols = st.columns(2)
            # Display the original name as static text
            row_cols[0].markdown(f"`{col}`")
            
            # Use the original column name as the key for the text input
            new_name = row_cols[1].text_input(
                label=f"Rename '{col}'",  # This label is hidden but good for accessibility
                value=col,
                key=f"rename_{col}",
                label_visibility="collapsed"
            )
            new_column_names[col] = new_name
    # --- ### END OF MODIFIED SECTION ### ---

    try:
        renamed_df = df.rename(columns=new_column_names)
    except Exception as e:
        st.error(f"Error renaming columns: {e}")
        st.stop()

    # Check if new column names are unique
    if len(set(renamed_df.columns)) != len(renamed_df.columns):
        st.error("Error: New column names are not unique. Please fix the duplicates.")
        st.stop()
        
    st.divider()

    with st.form("query_form"):
        st.subheader("⚙️ Generate SQL Queries")
        
        table_name = st.text_input("Enter MySQL table name", placeholder="e.g., users")
        
        form_cols = st.columns(3)
        chunk_size = form_cols[0].number_input(
            "Chunk Size (rows per query)",
            min_value=1,
            value=1000,
            help="Split the output into multiple INSERT statements of this size. Prevents 'max_allowed_packet' errors."
        )
        use_ignore = form_cols[1].checkbox(
            "Use 'INSERT IGNORE'",
            help="Uses 'INSERT IGNORE INTO' to skip rows that would cause duplicate key errors."
        )
        truncate_table = form_cols[2].checkbox(
            "Add 'TRUNCATE TABLE' ⚠️",
            help="Adds a 'TRUNCATE TABLE' command to the *start* of the script. This DELETES ALL DATA in the table first!"
        )

        submitted = st.form_submit_button("Generate Queries")

    if submitted and table_name:
        if not table_name.strip():
            st.warning("Please enter a table name.")
        else:
            with st.spinner("Generating SQL script..."):
                generated_queries = generate_chunked_insert_queries(
                    renamed_df, 
                    table_name, 
                    int(chunk_size), 
                    use_ignore
                )
                
                final_query_output = ""
                if truncate_table:
                    st.warning("The script includes `TRUNCATE TABLE` and will delete all existing data before inserting.")
                    final_query_output = f"TRUNCATE TABLE `{table_name}`;\n\n"
                
                final_query_output += generated_queries
                
                st.subheader("Generated Batch SQL Insert Script")
                
                st.download_button(
                    "Download SQL File",
                    data=final_query_output.encode('utf-8'),
                    file_name=f"{table_name}_batch_insert.sql",
                    mime="text/sql"
                )
                
                # Display a preview (e.g., first 5000 characters) to avoid crashing Streamlit
                preview = final_query_output[:5000]
                if len(final_query_output) > 5000:
                    preview += "\n\n... (preview truncated) ..."
                
                st.code(preview, language='sql')
