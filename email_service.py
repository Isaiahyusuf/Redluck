"""
Email Service for RedLuck Lotto Bot
Handles email verification and PIN reset via SendGrid
"""

import os
import random
import time
from datetime import datetime, timedelta
from typing import Optional, Dict

from db import get_db_conn, q

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@redluck.com")

CODE_EXPIRY_MINUTES = 10
MAX_ATTEMPTS_PER_HOUR = 3


def is_email_configured() -> bool:
    """Check if SendGrid is configured"""
    return bool(SENDGRID_API_KEY)


def generate_verification_code() -> str:
    """Generate a 6-digit verification code"""
    return str(random.randint(100000, 999999))


def save_verification_code(user_id: int, email: str, code: str, purpose: str) -> bool:
    """
    Save verification code to database with expiry
    purpose: 'verify_email' or 'reset_pin'
    """
    user_id = int(user_id)
    expires_at = datetime.utcnow() + timedelta(minutes=CODE_EXPIRY_MINUTES)
    
    conn = get_db_conn()
    c = conn.cursor()
    
    try:
        c.execute(q("""
            INSERT INTO email_verification_codes (user_id, email, code, purpose, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """), (user_id, email.lower(), code, purpose, expires_at.isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[Email] Error saving code: {e}")
        conn.close()
        return False


def verify_code(user_id: int, code: str, purpose: str) -> Dict:
    """
    Verify a code for a user
    Returns: {'valid': bool, 'email': str or None, 'error': str or None}
    """
    user_id = int(user_id)
    now = datetime.utcnow()
    
    conn = get_db_conn()
    c = conn.cursor()
    
    c.execute(q("""
        SELECT id, email, code, expires_at FROM email_verification_codes
        WHERE user_id = ? AND purpose = ? AND used = 0
        ORDER BY created_at DESC LIMIT 1
    """), (user_id, purpose))
    
    row = c.fetchone()
    
    if not row:
        conn.close()
        return {"valid": False, "email": None, "error": "No pending verification found"}
    
    code_id, email, stored_code, expires_at_str = row
    
    try:
        expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00').replace('+00:00', ''))
    except:
        expires_at = datetime.fromisoformat(str(expires_at_str)[:19])
    
    if now > expires_at:
        conn.close()
        return {"valid": False, "email": email, "error": "Code has expired"}
    
    if code != stored_code:
        conn.close()
        return {"valid": False, "email": email, "error": "Invalid code"}
    
    c.execute(q("UPDATE email_verification_codes SET used = 1 WHERE id = ?"), (code_id,))
    conn.commit()
    conn.close()
    
    return {"valid": True, "email": email, "error": None}


def can_send_code(user_id: int) -> bool:
    """Check if user can request another code (rate limiting)"""
    user_id = int(user_id)
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(q("""
        SELECT COUNT(*) FROM email_verification_codes
        WHERE user_id = ? AND created_at > ?
    """), (user_id, one_hour_ago))
    
    count = c.fetchone()[0]
    conn.close()
    
    return count < MAX_ATTEMPTS_PER_HOUR


async def send_verification_email(email: str, code: str, purpose: str = "verify") -> Dict:
    """
    Send verification code via SendGrid
    Returns: {'success': bool, 'error': str or None}
    """
    if not is_email_configured():
        print("[Email] SendGrid not configured")
        return {"success": False, "error": "Email service not configured"}
    
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        
        if purpose == "reset_pin":
            subject = "RedLuck Lotto - PIN Reset Code"
            content = f"""
            <div style="font-family: Arial, sans-serif; padding: 20px; max-width: 600px;">
                <h2 style="color: #f7931a;">PIN Reset Request</h2>
                <p>You requested to reset your PIN. Use this code to continue:</p>
                <div style="background: #f0f0f0; padding: 20px; margin: 20px 0; text-align: center;">
                    <h1 style="color: #4CAF50; letter-spacing: 8px; margin: 0;">{code}</h1>
                </div>
                <p>This code expires in {CODE_EXPIRY_MINUTES} minutes.</p>
                <p style="color: #666; font-size: 12px;">
                    If you didn't request this, please ignore this email and your PIN will remain unchanged.
                </p>
            </div>
            """
        else:
            subject = "RedLuck Lotto - Verify Your Email"
            content = f"""
            <div style="font-family: Arial, sans-serif; padding: 20px; max-width: 600px;">
                <h2 style="color: #f7931a;">Email Verification</h2>
                <p>Enter this code in the bot to verify your email:</p>
                <div style="background: #f0f0f0; padding: 20px; margin: 20px 0; text-align: center;">
                    <h1 style="color: #4CAF50; letter-spacing: 8px; margin: 0;">{code}</h1>
                </div>
                <p>This code expires in {CODE_EXPIRY_MINUTES} minutes.</p>
                <p style="color: #666; font-size: 12px;">
                    If you didn't request this, please ignore this email.
                </p>
            </div>
            """
        
        message = Mail(
            from_email=FROM_EMAIL,
            to_emails=email,
            subject=subject,
            html_content=content
        )
        
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        
        if response.status_code in [200, 201, 202]:
            print(f"[Email] Sent verification code to {email[:5]}***")
            return {"success": True, "error": None}
        else:
            return {"success": False, "error": f"SendGrid returned status {response.status_code}"}
            
    except Exception as e:
        print(f"[Email] Error sending email: {e}")
        return {"success": False, "error": str(e)}


def save_user_email(user_id: int, email: str, verified: bool = False) -> bool:
    """Save or update user's email"""
    user_id = int(user_id)
    email = email.lower().strip()
    
    conn = get_db_conn()
    c = conn.cursor()
    
    try:
        c.execute(q("""
            INSERT INTO user_emails (user_id, email, verified)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET email = ?, verified = ?
        """), (user_id, email, 1 if verified else 0, email, 1 if verified else 0))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[Email] Error saving email: {e}")
        conn.close()
        return False


def get_user_email(user_id: int) -> Optional[Dict]:
    """Get user's email info"""
    user_id = int(user_id)
    
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(q("SELECT email, verified FROM user_emails WHERE user_id = ?"), (user_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {"email": row[0], "verified": bool(row[1])}
    return None


def mark_email_verified(user_id: int) -> bool:
    """Mark user's email as verified"""
    user_id = int(user_id)
    
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(q("UPDATE user_emails SET verified = 1 WHERE user_id = ?"), (user_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    
    return affected > 0


def is_email_available(email: str, exclude_user_id: int = None) -> bool:
    """Check if email is available (not used by another user)"""
    email = email.lower().strip()
    
    conn = get_db_conn()
    c = conn.cursor()
    
    if exclude_user_id:
        c.execute(q("SELECT user_id FROM user_emails WHERE email = ? AND user_id != ?"), 
                  (email, int(exclude_user_id)))
    else:
        c.execute(q("SELECT user_id FROM user_emails WHERE email = ?"), (email,))
    
    row = c.fetchone()
    conn.close()
    
    return row is None
