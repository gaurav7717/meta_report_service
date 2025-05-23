import requests
import pandas as pd
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import logging
from flask import Flask, jsonify
import traceback

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Load environment variables
load_dotenv()
ACCESS_TOKEN = os.getenv('META_API_KEY')
ACCOUNT_ID = os.getenv('META_AD_ACCOUNT_ID')
EMAIL_SENDER = os.getenv('EMAIL_SENDER')
EMAIL_RECIPIENTS = os.getenv('EMAIL_RECIPIENTS', '').split(',')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
ENV = os.getenv('ENV', 'development')

# Check if required environment variables are set
if not ACCESS_TOKEN or not ACCOUNT_ID:
    logging.error("Required environment variables are missing. Please check your .env file.")
    raise ValueError("Required environment variables are missing")

# API endpoint and date
BASE_URL = f'https://graph.facebook.com/v20.0/act_{ACCOUNT_ID}/insights'
YESTERDAY = (datetime.now()).strftime('%Y-%m-%d')

# Define report directory
REPORT_DIR = os.path.dirname(os.path.abspath(__file__))

def fetch_meta_data():
    params = {
        'access_token': ACCESS_TOKEN,
        'fields': 'campaign_name,spend,impressions,clicks,actions,action_values',
        'time_range': f'{{"since":"{YESTERDAY}","until":"{YESTERDAY}"}}',
        'level': 'campaign',
        'limit': 100
    }
    try:
        response = requests.get(BASE_URL, params=params)
        response.raise_for_status()
        return response.json().get('data', [])
    except requests.exceptions.RequestException as e:
        logging.error(f"API request failed: {str(e)}")
        raise Exception(f"API request failed: {str(e)}")

def process_data(raw_data):
    if not raw_data:
        logging.warning("No data returned from API")
        return {
            'total_sales': 0,
            'total_ad_spend': 0,
            'overall_roas': 0,
            'overall_cpa': 0,
            'overall_ctr': 0,
            'overall_conversion_rate': 0,
            'total_impressions': 0,
            'total_clicks': 0,
            'campaign_summary': pd.DataFrame(),
            'high_roas_campaigns': pd.DataFrame(),
            'active_campaigns': pd.DataFrame()
        }
    
    df = pd.DataFrame(raw_data)
    
    # Convert data types with error handling
    df['spend'] = pd.to_numeric(df['spend'], errors='coerce').fillna(0).astype(float)
    df['impressions'] = pd.to_numeric(df['impressions'], errors='coerce').fillna(0).astype(int)
    df['clicks'] = pd.to_numeric(df['clicks'], errors='coerce').fillna(0).astype(int)
    
    # Extract conversions and sales
    df['conversions'] = df['actions'].apply(
        lambda x: next((float(item['value']) for item in x if item['action_type'] == 'purchase'), 0)
        if isinstance(x, list) else 0
    ).astype(int)
    df['sales'] = df['action_values'].apply(
        lambda x: next((float(item['value']) for item in x if item['action_type'] == 'purchase'), 0)
        if isinstance(x, list) else 0
    ).astype(float)
    
    # Calculate totals
    total_ad_spend = df['spend'].sum()
    total_sales = df['sales'].sum()
    total_impressions = df['impressions'].sum()
    total_clicks = df['clicks'].sum()
    total_conversions = df['conversions'].sum()
    
    # Calculate overall KPIs with safe division
    overall_roas = total_sales / total_ad_spend if total_ad_spend > 0 else 0
    overall_cpa = total_ad_spend / total_conversions if total_conversions > 0 else 0
    overall_ctr = (total_clicks / total_impressions) * 100 if total_impressions > 0 else 0
    overall_conversion_rate = (total_conversions / total_clicks) * 100 if total_clicks > 0 else 0
    
    # Campaign-level metrics
    campaign_summary = df.groupby('campaign_name').agg({
        'spend': 'sum',
        'sales': 'sum',
        'impressions': 'sum',
        'clicks': 'sum',
        'conversions': 'sum'
    }).reset_index()
    
    # Calculate campaign metrics with safe division
    campaign_summary['roas'] = (campaign_summary['sales'] / campaign_summary['spend']).replace([float('inf'), -float('inf')], 0).fillna(0)
    campaign_summary['cpa'] = (campaign_summary['spend'] / campaign_summary['conversions']).replace([float('inf'), -float('inf')], 0).fillna(0)
    campaign_summary['ctr'] = ((campaign_summary['clicks'] / campaign_summary['impressions']) * 100).replace([float('inf'), -float('inf')], 0).fillna(0)
    campaign_summary['conversion_rate'] = ((campaign_summary['conversions'] / campaign_summary['clicks']) * 100).replace([float('inf'), -float('inf')], 0).fillna(0)
    
    # Additional insights
    high_roas_campaigns = campaign_summary[campaign_summary['roas'] > 1]
    active_campaigns = campaign_summary[
        (campaign_summary['impressions'] > 0) | 
        (campaign_summary['clicks'] > 0) | 
        (campaign_summary['conversions'] > 0)
    ].sort_values(by='roas', ascending=False)
    
    return {
        'total_sales': total_sales,
        'total_ad_spend': total_ad_spend,
        'overall_roas': overall_roas,
        'overall_cpa': overall_cpa,
        'overall_ctr': overall_ctr,
        'overall_conversion_rate': overall_conversion_rate,
        'total_impressions': total_impressions,
        'total_clicks': total_clicks,
        'campaign_summary': campaign_summary,
        'high_roas_campaigns': high_roas_campaigns,
        'active_campaigns': active_campaigns
    }

def generate_pdf_report(metrics):
    report_name = os.path.join(REPORT_DIR, f"report_{YESTERDAY}.pdf")
    c = canvas.Canvas(report_name, pagesize=letter)
    width, height = letter
    
    # Define margins (50 points on all sides)
    margin = 50
    table_width = width - 2 * margin  # Usable width for tables
    
    # Starting Y position with top margin
    y = height - margin

    # Title
    date_part = datetime.now().strftime("%Y-%m-%d")
    time_part = datetime.now().strftime("%I %p").lstrip("0")  # e.g., "11 AM" instead of "011 AM"
    timestamp = f"{date_part} / {time_part}"
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, y, f"Daily Marketing Performance Report ({timestamp})")
    y -= 20
    
    # Summary Metrics Table
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, "Summary Metrics")
    y -= 30
    
    # Draw table for summary metrics
    headers = ["Metric", "Value"]
    col_widths = [200, 200]  # Adjusted widths for summary table
    x_positions = [margin, margin + col_widths[0]]
    
    # Header row with background
    c.setFillColorRGB(0.9, 0.9, 0.9)  # Light gray background
    c.rect(margin, y - 5, sum(col_widths), 20, fill=True, stroke=False)
    c.setFillColorRGB(0, 0, 0)  # Reset to black for text
    c.setFont("Helvetica-Bold", 12)
    for i, header in enumerate(headers):
        c.drawString(x_positions[i] + 5, y, header)  # 5 points padding
    y -= 20
    
    # Draw table rows with borders
    summary_metrics = [
        ("Date", YESTERDAY),
        ("Total Sales", f"Rs {metrics['total_sales']:.2f}"),
        ("Total Ad Spend", f"Rs {metrics['total_ad_spend']:.2f}"),
        ("Overall ROAS", f"{metrics['overall_roas']:.2f}"),
        ("Overall CPA", f"Rs {metrics['overall_cpa']:.2f}"),
        ("Overall CTR", f"{metrics['overall_ctr']:.2f}%"),
        ("Overall Conversion Rate", f"{metrics['overall_conversion_rate']:.2f}%"),
        ("Total Impressions", str(metrics['total_impressions'])),
        ("Total Clicks", str(metrics['total_clicks'])),
    ]
    c.setFont("Helvetica", 12)
    for metric, value in summary_metrics:
        if y < margin:
            c.showPage()
            y = height - margin
            c.setFillColorRGB(0.9, 0.9, 0.9)
            c.rect(margin, y - 5, sum(col_widths), 20, fill=True, stroke=False)
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 12)
            for i, header in enumerate(headers):
                c.drawString(x_positions[i] + 5, y, header)
            y -= 20
            c.setFont("Helvetica", 12)
        
        # Draw cell borders
        c.rect(margin, y - 5, col_widths[0], 20, stroke=True, fill=False)
        c.rect(margin + col_widths[0], y - 5, col_widths[1], 20, stroke=True, fill=False)
        
        # Draw text with padding
        c.drawString(x_positions[0] + 5, y, metric)
        c.drawString(x_positions[1] + 5, y, value)
        y -= 20
    
    # Campaigns with ROAS > 1 Table
    y -= 30
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, "Campaigns with ROAS > 1")
    y -= 30
    
    if not metrics['high_roas_campaigns'].empty:
        headers = ["Campaign Name", "Spend", "Sales", "ROAS", "CPA", "CTR", "CR"]
        col_widths = [150, 80, 80, 40, 80, 40, 40]  # Adjusted for longer campaign names
        x_positions = [margin]
        for i in range(len(col_widths) - 1):
            x_positions.append(x_positions[i] + col_widths[i])
        
        # Header row with background
        c.setFillColorRGB(0.9, 0.9, 0.9)
        c.rect(margin, y - 5, sum(col_widths), 20, fill=True, stroke=False)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 12)
        for i, header in enumerate(headers):
            c.drawString(x_positions[i] + 5, y, header)
        y -= 20
        
        # Draw table rows with borders
        c.setFont("Helvetica", 12)
        for _, row in metrics['high_roas_campaigns'].iterrows():
            if y < margin:
                c.showPage()
                y = height - margin
                c.setFillColorRGB(0.9, 0.9, 0.9)
                c.rect(margin, y - 5, sum(col_widths), 20, fill=True, stroke=False)
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica-Bold", 12)
                for i, header in enumerate(headers):
                    c.drawString(x_positions[i] + 5, y, header)
                y -= 20
                c.setFont("Helvetica", 12)
            
            # Draw cell borders
            for i in range(len(col_widths)):
                c.rect(x_positions[i], y - 5, col_widths[i], 20, stroke=True, fill=False)
            
            # Draw text with padding
            campaign_name = row['campaign_name'][:25] + '...' if len(row['campaign_name']) > 25 else row['campaign_name']
            c.drawString(x_positions[0] + 5, y, campaign_name)
            c.drawString(x_positions[1] + 5, y, f"Rs {row['spend']:.2f}")
            c.drawString(x_positions[2] + 5, y, f"Rs {row['sales']:.2f}")
            c.drawString(x_positions[3] + 5, y, f"{row['roas']:.2f}")
            c.drawString(x_positions[4] + 5, y, f"Rs {row['cpa']:.2f}")
            c.drawString(x_positions[5] + 5, y, f"{row['ctr']:.2f}%")
            c.drawString(x_positions[6] + 5, y, f"{row['conversion_rate']:.2f}%")
            y -= 20
    else:
        c.setFont("Helvetica", 12)
        c.drawString(margin, y, "No campaigns with ROAS > 1")
        y -= 20
    
    # Active Campaigns Table
    y -= 30
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, "Active Campaigns")
    y -= 30
    
    if not metrics['active_campaigns'].empty:
        headers = ["Campaign Name", "Spend", "Sales", "ROAS", "CPA", "CTR", "CR"]
        col_widths = [150, 80, 80, 40, 80, 40, 40]  # Same widths as above
        x_positions = [margin]
        for i in range(len(col_widths) - 1):
            x_positions.append(x_positions[i] + col_widths[i])
        
        # Header row with background
        c.setFillColorRGB(0.9, 0.9, 0.9)
        c.rect(margin, y - 5, sum(col_widths), 20, fill=True, stroke=False)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 12)
        for i, header in enumerate(headers):
            c.drawString(x_positions[i] + 5, y, header)
        y -= 20
        
        # Draw table rows with borders
        c.setFont("Helvetica", 12)
        for _, row in metrics['active_campaigns'].iterrows():
            if y < margin:
                c.showPage()
                y = height - margin
                c.setFillColorRGB(0.9, 0.9, 0.9)
                c.rect(margin, y - 5, sum(col_widths), 20, fill=True, stroke=False)
                c.setFillColorRGB(0, 0, 0)
                c.setFont("Helvetica-Bold", 12)
                for i, header in enumerate(headers):
                    c.drawString(x_positions[i] + 5, y, header)
                y -= 20
                c.setFont("Helvetica", 12)
            
            # Draw cell borders
            for i in range(len(col_widths)):
                c.rect(x_positions[i], y - 5, col_widths[i], 20, stroke=True, fill=False)
            
            # Draw text with padding
            campaign_name = row['campaign_name'][:25] + '...' if len(row['campaign_name']) > 25 else row['campaign_name']
            c.drawString(x_positions[0] + 5, y, campaign_name)
            c.drawString(x_positions[1] + 5, y, f"Rs {row['spend']:.2f}")
            c.drawString(x_positions[2] + 5, y, f"Rs {row['sales']:.2f}")
            c.drawString(x_positions[3] + 5, y, f"{row['roas']:.2f}")
            c.drawString(x_positions[4] + 5, y, f"Rs {row['cpa']:.2f}")
            c.drawString(x_positions[5] + 5, y, f"{row['ctr']:.2f}%")
            c.drawString(x_positions[6] + 5, y, f"{row['conversion_rate']:.2f}%")
            y -= 20
        
        # Calculate and display total spend and sales
        total_spend = metrics['active_campaigns']['spend'].sum()
        total_sales = metrics['active_campaigns']['sales'].sum()
        
        # Summary row with background
        if y < margin:
            c.showPage()
            y = height - margin
        
        c.setFillColorRGB(0.95, 0.95, 0.95)  # Slightly darker gray for summary row
        c.rect(margin, y - 5, sum(col_widths), 20, fill=True, stroke=False)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x_positions[0] + 5, y, "Total")
        c.drawString(x_positions[1] + 5, y, f"Rs {total_spend:.2f}")
        c.drawString(x_positions[2] + 5, y, f"Rs {total_sales:.2f}")
        # Draw borders for summary row
        for i in range(len(col_widths)):
            c.rect(x_positions[i], y - 5, col_widths[i], 20, stroke=True, fill=False)
        y -= 20
    else:
        c.setFont("Helvetica", 12)
        c.drawString(margin, y, "No active campaigns")
        y -= 20
    
    c.save()
    logging.info(f"PDF report saved to: {report_name}")
    return report_name

def send_email(report_file):
    if not EMAIL_PASSWORD:
        logging.error("Email password not set in environment variables")
        raise ValueError("Email password not set")
    
    msg = MIMEMultipart()
    msg['From'] = EMAIL_SENDER
    msg['To'] = ", ".join(EMAIL_RECIPIENTS)
    msg['Subject'] = f"Daily Marketing Report - {YESTERDAY}"
    
    body = f"Attached is the daily marketing performance report for {YESTERDAY}."
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        with open(report_file, "rb") as attachment:
            part = MIMEApplication(attachment.read(), Name=os.path.basename(report_file))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(report_file)}"'
            msg.attach(part)
    except FileNotFoundError:
        logging.error(f"Report file not found: {report_file}")
        raise
    
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        logging.info("Email sent successfully")
    except smtplib.SMTPException as e:
        logging.error(f"Failed to send email: {str(e)}")
        raise

app = Flask(__name__)

@app.route('/generate-report', methods=['GET'])
def generate_report():
    try:
        logging.info("Report generation started...")
        data = fetch_meta_data()
        metrics = process_data(data)
        report_file = generate_pdf_report(metrics)
        send_email(report_file)
        logging.info("Report generation and email completed successfully.")
        return jsonify({
            'status': 'success',
            'message': 'Report generated and sent via email.',
            'report_file': report_file
        })
    except Exception as e:
        logging.error(f"Error during report generation: {str(e)}")
        traceback_str = traceback.format_exc()
        return jsonify({
            'status': 'error',
            'message': str(e),
            'trace': traceback_str
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    if ENV == 'production':
        app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
    else:
        app.run(debug=True)