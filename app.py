from flask import Flask, render_template, request, redirect, url_for, jsonify, Response, send_file, session, flash
from datetime import datetime, timedelta
import json
import time
import random
import logging
from collections import deque
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from functools import wraps
import atexit
from reportlab.lib.units import inch
import os
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# Database connection helper
def get_db_connection():
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        port=os.getenv('DB_PORT')
    )
    conn.autocommit = True
    return conn

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Helper functions - make sure these are the ONLY definitions of these functions
def get_low_stock_products(user_email):
    """Get low stock products for specific user"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute("SELECT * FROM inventory WHERE user_id = %s AND quantity <= min_stock AND quantity > 0 ORDER BY quantity ASC LIMIT 5", (session['user_id'],))
    low_stock_products = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return low_stock_products

def get_category_data(user_email):
    """Get category data for specific user"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute("SELECT * FROM inventory WHERE user_id = %s", (session['user_id'],))
    inventory = cur.fetchall()
    
    categories = {}
    category_prices = {}
    
    for item in inventory:
        category = item.get('category', 'Uncategorized')
        quantity = item.get('quantity', 0)
        price = item.get('price', 0) * quantity
        
        # Update quantities
        if category in categories:
            categories[category] += quantity
        else:
            categories[category] = quantity
            
        # Update prices
        if category in category_prices:
            category_prices[category] += price
        else:
            category_prices[category] = price
    
    # Ensure we have at least some data
    if not categories:
        categories['No Data'] = 0
        category_prices['No Data'] = 0
        
    # Sort categories by total price
    sorted_categories = sorted(category_prices.items(), key=lambda x: x[1], reverse=True)
    labels = [item[0] for item in sorted_categories]
    price_data = [item[1] for item in sorted_categories]
    quantity_data = [categories[label] for label in labels]
    
    cur.close()
    conn.close()
    
    return {
        'labels': labels,
        'price_data': price_data,
        'quantity_data': quantity_data
    }

def get_sales_data(user_email):
    """Get sales data for charts"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Product-wise sales data
    cur.execute("SELECT item_name, SUM(quantity) as total_quantity, SUM(price * quantity) as total_revenue FROM order_items WHERE user_id = %s GROUP BY item_name ORDER BY total_revenue DESC LIMIT 10", (session['user_id'],))
    product_sales = cur.fetchall()
    
    # Today's sales data (by hour)
    today = datetime.now().date()
    hourly_sales = {i: {'revenue': 0, 'quantity': 0} for i in range(24)}  # Initialize all hours
    
    cur.execute("SELECT EXTRACT(HOUR FROM created_at) as hour, SUM(total) as total_revenue, SUM(quantity) as total_quantity FROM orders WHERE user_id = %s AND DATE(created_at) = %s GROUP BY hour", (session['user_id'], today))
    today_sales = cur.fetchall()
    
    for sale in today_sales:
        hour = int(sale['hour'])
        hourly_sales[hour]['revenue'] = sale['total_revenue']
        hourly_sales[hour]['quantity'] = sale['total_quantity']
    
    cur.close()
    conn.close()
    
    return {
        'product': {
            'labels': [item['item_name'] for item in product_sales],
            'revenue_data': [item['total_revenue'] for item in product_sales],
            'quantity_data': [item['total_quantity'] for item in product_sales]
        },
        'today': {
            'labels': [f'{i:02d}:00' for i in range(24)],
            'data': [hourly_sales[i]['revenue'] for i in range(24)],
            'quantity_data': [hourly_sales[i]['quantity'] for i in range(24)]
        }
    }

def get_inventory_data(user_email):
    """Get both category and item-wise inventory data"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Initialize empty dictionaries
    categories = {}
    category_prices = {}
    items = {}
    item_prices = {}
    
    # Process inventory data
    cur.execute("SELECT * FROM inventory WHERE user_id = %s", (session['user_id'],))
    inventory = cur.fetchall()
    
    for item in inventory:
        category = item.get('category', 'Uncategorized')
        name = item.get('name', 'Unknown')
        quantity = float(item.get('quantity', 0))
        price = float(item.get('price', 0)) * quantity
        
        # Update category data
        if category in categories:
            categories[category] += quantity
            category_prices[category] += price
        else:
            categories[category] = quantity
            category_prices[category] = price
            
        # Update item data
        if name in items:
            items[name] += quantity
            item_prices[name] += price
        else:
            items[name] = quantity
            item_prices[name] = price
    
    # Ensure we have at least some data
    if not categories:
        categories['No Data'] = 0
        category_prices['No Data'] = 0
    
    if not items:
        items['No Items'] = 0
        item_prices['No Items'] = 0
    
    # Sort categories by total price
    sorted_categories = sorted(category_prices.items(), key=lambda x: x[1], reverse=True)
    category_labels = [item[0] for item in sorted_categories]
    category_price_data = [item[1] for item in sorted_categories]
    category_quantity_data = [categories[label] for label in category_labels]
    
    # Sort items by total price
    sorted_items = sorted(item_prices.items(), key=lambda x: x[1], reverse=True)
    item_labels = [item[0] for item in sorted_items]
    item_price_data = [item[1] for item in sorted_items]
    item_quantity_data = [items[label] for label in item_labels]
    
    cur.close()
    conn.close()
    
    return {
        'category': {
            'labels': category_labels,
            'price_data': category_price_data,
            'quantity_data': category_quantity_data
        },
        'item': {
            'labels': item_labels,
            'price_data': item_price_data,
            'quantity_data': item_quantity_data
        }
    }

def get_forecasting_data(user_email):
    """Get forecasting data for specific user"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    forecast = {}
    
    # Calculate average daily sales for each product
    cur.execute("SELECT item_name, SUM(quantity) as total_quantity, COUNT(DISTINCT DATE(created_at)) as days_with_sales FROM order_items WHERE user_id = %s AND created_at >= NOW() - INTERVAL '30 days' GROUP BY item_name", (session['user_id'],))
    sales_data = cur.fetchall()
    
    for sale in sales_data:
        name = sale['item_name']
        total_quantity = sale['total_quantity']
        days_with_sales = sale['days_with_sales']
        
        avg_daily_sales = total_quantity / max(days_with_sales, 1)
        forecast[name] = max(0, avg_daily_sales * 7)  # 7-day forecast
    
    # Sort by forecasted quantity
    sorted_forecast = sorted(forecast.items(), 
                           key=lambda x: x[1],
                           reverse=True)[:10]  # Top 10 items
    
    # Ensure we have at least some data
    if not sorted_forecast:
        return {
            'labels': ['No Data'],
            'data': [0]
        }
    
    cur.close()
    conn.close()
    
    return {
        'labels': [item[0] for item in sorted_forecast],
        'data': [item[1] for item in sorted_forecast]
    }

def format_indian_currency(amount):
    s = f"{amount:.2f}"
    integer_part, decimal_part = s.split(".")
    integer_part = "{:,}".format(int(integer_part)).replace(",", ",")
    return f"₹{integer_part}.{decimal_part}"

def cleanup():
    """Function to clear in-memory data."""
    print("Cleanup: Cleared all in-memory data.")

# Register the cleanup function to be called on exit
atexit.register(cleanup)

# Home route (redirects to login or dashboard based on session)
@app.route('/')
def home():
    if 'user_email' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Check if user exists
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        
        cur.close()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            session['user_email'] = email
            session['username'] = user['username']
            session['user_id'] = user['id']
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'error')
    
    return render_template('login.html', company_name="Inventory Dashboard")

# Registration route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        company_name = request.form.get('company_name', '')
        
        # Hash the password
        hashed_password = generate_password_hash(password)
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Check if user already exists
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close()
            conn.close()
            flash('Email already registered', 'error')
            return render_template('register.html')
        
        # Insert new user
        cur.execute(
            "INSERT INTO users (username, email, password, company_name) VALUES (%s, %s, %s, %s) RETURNING id",
            (username, email, hashed_password, company_name)
        )
        user_id = cur.fetchone()[0]
        
        # Insert default settings
        cur.execute(
            "INSERT INTO settings (user_id, setting_key, setting_value) VALUES (%s, %s, %s)",
            (user_id, 'currency', '₹')
        )
        
        conn.commit()
        cur.close()
        conn.close()
        
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

# Logout route
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# Dashboard route (protected)
@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    user_id = session['user_id']
    
    # Get inventory count
    cur.execute("SELECT COUNT(*) FROM inventory WHERE user_id = %s", (user_id,))
    inventory_count = cur.fetchone()[0]
    
    # Get categories count
    cur.execute("SELECT COUNT(*) FROM categories WHERE user_id = %s", (user_id,))
    categories_count = cur.fetchone()[0]
    
    # Get orders count
    cur.execute("SELECT COUNT(*) FROM orders WHERE user_id = %s", (user_id,))
    orders_count = cur.fetchone()[0]
    
    # Get total stock value
    cur.execute(
        "SELECT COALESCE(SUM(quantity * price), 0) FROM inventory WHERE user_id = %s", 
        (user_id,)
    )
    stock_value = cur.fetchone()[0]
    
    # Get total sales (sum of all orders)
    cur.execute(
        "SELECT COALESCE(SUM(total), 0) FROM orders WHERE user_id = %s",
        (user_id,)
    )
    total_sales = cur.fetchone()[0]
    
    # Get low stock items - RENAMED TO low_stock_products
    cur.execute("""
        SELECT * FROM inventory 
        WHERE user_id = %s AND quantity <= min_stock AND quantity > 0
        ORDER BY quantity ASC
        LIMIT 5
    """, (user_id,))
    low_stock_products = cur.fetchall()  # Changed variable name from low_stock to low_stock_products
    
    # Get recent orders
    cur.execute("""
        SELECT o.id, o.order_number, o.customer, o.total, o.created_at,
               to_char(o.created_at, 'YYYY-MM-DD') as formatted_date
        FROM orders o 
        WHERE o.user_id = %s 
        ORDER BY o.created_at DESC 
        LIMIT 5
    """, (user_id,))
    orders = cur.fetchall()
    
    # Get recent activities
    cur.execute("""
        SELECT *, to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as formatted_date
        FROM history 
        WHERE user_id = %s 
        ORDER BY created_at DESC 
        LIMIT 10
    """, (user_id,))
    recent_activities = cur.fetchall()
    
    # Get company name
    cur.execute("SELECT company_name FROM users WHERE id = %s", (user_id,))
    user_result = cur.fetchone()
    company_name = user_result['company_name'] if user_result else ''
    
    cur.close()
    conn.close()
    
    return render_template(
        'dashboard.html',
        inventory_count=inventory_count,
        categories_count=categories_count,
        orders_count=orders_count,
        stock_value=stock_value,
        total_sales=total_sales,
        low_stock_products=low_stock_products,  # Changed from low_stock to low_stock_products
        orders=orders,
        recent_activities=recent_activities,
        company_name=company_name
    )

# Orders route (protected)
@app.route('/orders')
@login_required
def orders_page():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    user_id = session['user_id']
    
    # Get all orders
    cur.execute("""
        SELECT * FROM orders
        WHERE user_id = %s
        ORDER BY id DESC
    """, (user_id,))
    
    orders_data = cur.fetchall()
    
    # Convert DictRow objects to regular dictionaries to allow adding keys
    orders = []
    for order_row in orders_data:
        # Convert to regular dictionary
        order = dict(order_row)
        
        # Get items for this order
        cur.execute("""
            SELECT oi.*, i.name as item_name, i.category_id, c.name as category_name
            FROM order_items oi
            JOIN inventory i ON oi.item_id = i.id
            LEFT JOIN categories c ON i.category_id = c.id
            WHERE oi.order_id = %s
        """, (order['id'],))
        
        order_items = cur.fetchall()
        
        # Convert order items to regular dictionaries too
        items = []
        for item in order_items:
            items.append(dict(item))
        
        # Add items to the order dictionary
        order['items'] = items
        orders.append(order)
    
    # Get currency setting
    cur.execute("""
        SELECT setting_value FROM settings
        WHERE user_id = %s AND setting_key = 'currency'
    """, (user_id,))
    currency_result = cur.fetchone()
    currency = currency_result['setting_value'] if currency_result else '₹'
    
    cur.close()
    conn.close()
    
    return render_template('orders.html', orders=orders, currency=currency)

# Add order route (protected)
@app.route('/add_order', methods=['POST'])
@login_required
def add_order():
    try:
        user_id = session['user_id']
        
        # Check if the request has JSON data or form data
        if request.is_json:
            data = request.get_json()
        else:
            # Handle form data
            data = {
                'customer': request.form.get('customer', 'Walk-in Customer'),
                'order_items': request.form.getlist('order_items[]'),
                'quantities': request.form.getlist('quantities[]'),
                'prices': request.form.getlist('prices[]')
            }
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Generate an order number (format: ORD-YYYYMMDD-XXXX)
        from datetime import datetime
        today = datetime.now().strftime('%Y%m%d')
        
        # Get the latest order number for today to increment
        cur.execute(
            """
            SELECT MAX(order_number) as max_number FROM orders 
            WHERE order_number LIKE %s
            """, 
            (f'ORD-{today}-%',)
        )
        
        result = cur.fetchone()
        if result and result['max_number']:
            # Extract the sequence number and increment
            last_seq = int(result['max_number'].split('-')[-1])
            new_seq = last_seq + 1
        else:
            # First order of the day
            new_seq = 1
            
        # Format order number with leading zeros (4 digits)
        order_number = f'ORD-{today}-{new_seq:04d}'
        
        # Calculate the total order amount
        total = 0
        for i in range(len(data['order_items'])):
            quantity = int(data['quantities'][i])
            price = float(data['prices'][i])
            total += quantity * price
        
        # Insert the order with order_number, customer and total
        cur.execute(
            """
            INSERT INTO orders 
            (user_id, order_number, customer, total, status) 
            VALUES (%s, %s, %s, %s, 'pending')
            RETURNING id
            """,
            (user_id, order_number, data.get('customer', 'Walk-in Customer'), total)
        )
        order_id = cur.fetchone()['id']
        
        # Insert order items
        for i in range(len(data['order_items'])):
            item_id = int(data['order_items'][i])
            quantity = int(data['quantities'][i])
            price = float(data['prices'][i])
            
            # Get the item name from the inventory table
            cur.execute(
                """
                SELECT name FROM inventory
                WHERE id = %s AND user_id = %s
                """,
                (item_id, user_id)
            )
            
            item_result = cur.fetchone()
            if not item_result:
                raise Exception(f"Item with ID {item_id} not found")
                
            item_name = item_result['name']
            
            # Insert order item with the item name
            cur.execute(
                """
                INSERT INTO order_items
                (order_id, item_id, item_name, quantity, price)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (order_id, item_id, item_name, quantity, price)
            )
            
            # Update inventory quantity
            cur.execute(
                """
                UPDATE inventory
                SET quantity = quantity - %s
                WHERE id = %s AND user_id = %s
                """,
                (quantity, item_id, user_id)
            )
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "order_id": order_id,
            "order_number": order_number,
            "total": total,
            "message": "Order created successfully"
        })
        
    except Exception as e:
        print(f"Error creating order: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# Protect all other routes
@app.before_request
def require_login():
    allowed_routes = ['login', 'register', 'static']
    if request.endpoint not in allowed_routes and 'username' not in session:
        flash('Please login to access this page.', 'error')
        return redirect(url_for('login'))

# Inventory route
@app.route('/inventory')
@login_required
def inventory():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        user_id = session['user_id']
        
        # Get inventory items
        cur.execute("""
            SELECT i.*, c.name as category_name 
            FROM inventory i
            LEFT JOIN categories c ON i.category_id = c.id
            WHERE i.user_id = %s
            ORDER BY i.name ASC
        """, (user_id,))
        items = cur.fetchall()
        
        # Print for debugging
        print(f"Retrieved {len(items)} inventory items for user {user_id}")
        
        # Get categories for dropdown
        cur.execute("""
            SELECT * FROM categories
            WHERE user_id = %s
            ORDER BY name ASC
        """, (user_id,))
        categories = cur.fetchall()
        
        # Get currency setting
        cur.execute("""
            SELECT setting_value FROM settings
            WHERE user_id = %s AND setting_key = 'currency'
        """, (user_id,))
        currency_result = cur.fetchone()
        currency = currency_result['setting_value'] if currency_result else '₹'
        
        cur.close()
        conn.close()
        
        # Convert DictRow objects to regular dictionaries to avoid template issues
        items_list = [dict(item) for item in items]
        categories_list = [dict(category) for category in categories]
        
        return render_template(
            'inventory.html',
            items=items_list,
            categories=categories_list,
            currency=currency
        )
    
    except Exception as e:
        print(f"Error in inventory route: {str(e)}")
        # Return an error message to help with debugging
        return f"An error occurred: {str(e)}", 500

# Create a separate route for category management
@app.route('/manage_categories')
@login_required
def manage_categories():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    user_id = session['user_id']
    
    # Get all categories
    cur.execute("""
        SELECT * FROM categories
        WHERE user_id = %s
        ORDER BY name ASC
    """, (user_id,))
    categories = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template(
        'manage_categories.html',
        categories=categories
    )

# Add a simple route to add categories
@app.route('/add_category', methods=['POST'])
@login_required
def add_category():
    try:
        user_id = session['user_id']
        name = request.form.get('name', '').strip()
        
        if not name:
            return jsonify({
                "success": False,
                "message": "Category name is required"
            })
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Check for existing category
        cur.execute("SELECT id FROM categories WHERE name = %s AND user_id = %s", (name, user_id))
        existing = cur.fetchone()
        
        if existing:
            cur.close()
            conn.close()
            return jsonify({
                "success": True,
                "id": existing['id'],
                "message": "Category already exists"
            })
        
        # Insert new category
        cur.execute(
            "INSERT INTO categories (name, user_id) VALUES (%s, %s) RETURNING id", 
            (name, user_id)
        )
        new_id = cur.fetchone()['id']
        conn.commit()
        
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "id": new_id,
            "message": "Category added successfully"
        })
        
    except Exception as e:
        print(f"Error adding category: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"Error: {str(e)}"
        })

@app.route('/history')
@login_required
def history_page():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    user_id = session['user_id']
    
    # Get all history items
    cur.execute("""
        SELECT *, to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as formatted_date
        FROM history 
        WHERE user_id = %s 
        ORDER BY created_at DESC
    """, (user_id,))
    history_items = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('history.html', history=history_items)

@app.route('/settings')
@login_required
def settings_page():
    user_email = session['user_email']
    user_data = init_user_if_needed(user_email)
    return render_template('settings.html', 
                         company_name=user_data['company_name'])

@app.route('/update_company_name', methods=['POST'])
@login_required
def update_company_name():
    try:
        data = request.get_json()
        new_name = data.get('company_name', '').strip()
        
        if not new_name:
            return jsonify({
                "success": False,
                "message": "Company name cannot be empty"
            }), 400
        
        user_id = session['user_id']
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Update company name
        cur.execute(
            "UPDATE users SET company_name = %s WHERE id = %s",
            (new_name, user_id)
        )
        
        # Add to history
        cur.execute(
            "INSERT INTO history (user_id, action, details) VALUES (%s, %s, %s)",
            (user_id, 'Company Name Updated', f'Changed to {new_name}')
        )
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "Company name updated successfully"
        })
        
    except Exception as e:
        print(f"Error updating company name: {str(e)}")
        return jsonify({
            "success": False,
            "message": "Failed to update company name"
        }), 500

@app.route('/analytics')
@login_required
def analytics_page():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    user_id = session['user_id']
    
    # Get sales data by month
    cur.execute("""
        SELECT 
            to_char(created_at, 'YYYY-MM') as month,
            SUM(total) as revenue
        FROM orders
        WHERE user_id = %s
        GROUP BY month
        ORDER BY month ASC
    """, (user_id,))
    monthly_sales = cur.fetchall()
    
    # Format monthly sales for JSON chart data
    sales_data = []
    for sale in monthly_sales:
        sales_data.append({
            'month': sale['month'],
            'revenue': float(sale['revenue']) if sale['revenue'] else 0
        })
    
    # Get top selling products
    cur.execute("""
        SELECT 
            i.name,
            SUM(oi.quantity) as total_sold
        FROM order_items oi
        JOIN inventory i ON oi.item_id = i.id
        JOIN orders o ON oi.order_id = o.id
        WHERE o.user_id = %s
        GROUP BY i.name
        ORDER BY total_sold DESC
        LIMIT 5
    """, (user_id,))
    top_products = cur.fetchall()
    
    # Get inventory value by category
    cur.execute("""
        SELECT 
            COALESCE(c.name, 'Uncategorized') as category,
            SUM(i.quantity * i.price) as value
        FROM inventory i
        LEFT JOIN categories c ON i.category_id = c.id
        WHERE i.user_id = %s
        GROUP BY c.name
        ORDER BY value DESC
    """, (user_id,))
    category_values = cur.fetchall()
    
    # Get inventory data for charts
    cur.execute("""
        SELECT 
            i.name, 
            i.quantity,
            i.price,
            COALESCE(c.name, 'Uncategorized') as category
        FROM inventory i
        LEFT JOIN categories c ON i.category_id = c.id
        WHERE i.user_id = %s
    """, (user_id,))
    inventory_data = cur.fetchall()
    
    # Convert to list of dicts for JSON serialization
    inventory_data_list = []
    for item in inventory_data:
        inventory_data_list.append({
            'name': item['name'],
            'quantity': item['quantity'],
            'price': float(item['price']),
            'category': item['category']
        })
    
    # Get currency setting
    cur.execute("""
        SELECT setting_value FROM settings
        WHERE user_id = %s AND setting_key = 'currency'
    """, (user_id,))
    currency_result = cur.fetchone()
    currency = currency_result['setting_value'] if currency_result else '₹'
    
    cur.close()
    conn.close()
    
    return render_template(
        'analytics.html',
        monthly_sales=monthly_sales,
        top_products=top_products,
        category_values=category_values,
        inventory_data=inventory_data_list,
        sales_data=sales_data,
        currency=currency
    )

def get_sales_mini_data(user_id):
    """Get recent sales data for mini chart"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Get recent orders sorted by date
    cur.execute("""
        SELECT id, total, created_at
        FROM orders 
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 7
    """, (user_id,))
    recent_orders = cur.fetchall()
    
    # Format data for chart
    data = [float(order['total']) for order in recent_orders]
    labels = [order['created_at'].strftime("%d/%m") for order in recent_orders]
    
    cur.close()
    conn.close()
    
    return {
        'labels': labels,
        'data': data
    }

def get_inventory_mini_data(user_id):
    """Get inventory data for mini chart"""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Get top items by quantity
    cur.execute("""
        SELECT name, quantity
        FROM inventory
        WHERE user_id = %s
        ORDER BY quantity DESC
        LIMIT 7
    """, (user_id,))
    top_items = cur.fetchall()
    
    # Format data for chart
    labels = [item['name'] for item in top_items]
    data = [item['quantity'] for item in top_items]
    
    cur.close()
    conn.close()
    
    return {
        'labels': labels,
        'data': data
    }

def get_product_sales_data(user_email):
    """Get product sales data"""
    user_data = users[user_email]
    product_sales = {}
    for order in user_data['orders']:
        for item in order['items']:
            name = item['name']
            if name not in product_sales:
                product_sales[name] = 0
            product_sales[name] += item['quantity'] * item['price']
    
    # Sort by sales value and get top 5
    sorted_sales = sorted(product_sales.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        'labels': [item[0] for item in sorted_sales],
        'data': [item[1] for item in sorted_sales]
    }

def get_today_sales_data(user_email):
    """Get today's sales data"""
    user_data = users[user_email]
    today = datetime.now().date()
    today_sales = {}
    
    for order in user_data['orders']:
        order_date = datetime.strptime(order['date'], "%Y-%m-%d %H:%M:%S").date()
        if order_date == today:
            for item in order['items']:
                name = item['name']
                if name not in today_sales:
                    today_sales[name] = 0
                today_sales[name] += item['quantity'] * item['price']
    
    return {
        'labels': list(today_sales.keys()),
        'data': list(today_sales.values())
    }

def get_top_products(user_email):
    """Get top selling products"""
    user_data = users[user_email]
    orders = user_data.get('orders', [])
    
    # Aggregate product sales
    product_sales = {}
    for order in orders:
        for item in order.get('items', []):
            name = item.get('name', 'Unknown')
            quantity = item.get('quantity', 0)
            price = item.get('price', 0)
            revenue = price * quantity
            
            if name in product_sales:
                product_sales[name]['quantity'] += quantity
                product_sales[name]['revenue'] += revenue
            else:
                product_sales[name] = {
                    'name': name,
                    'quantity': quantity,
                    'revenue': revenue
                }
    
    # Convert to list and sort by revenue
    top_products = list(product_sales.values())
    top_products.sort(key=lambda x: x['revenue'], reverse=True)
    
    return top_products[:5]  # Return top 5 products

@app.route('/generate_report')
@login_required
def generate_report():
    user_email = session['user_email']
    user_data = users[user_email]
    
    # Create a PDF buffer
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=30,
        alignment=1  # Center alignment
    )
    
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Heading2'],
        fontSize=16,
        spaceAfter=20,
        textColor=colors.HexColor('#666666')
    )
    
    # Add company name and report title
    elements.append(Paragraph(user_data.get('company_name', 'Company Name'), title_style))
    elements.append(Paragraph('Report', subtitle_style))
    
    # Add date range
    current_date = datetime.now().strftime("%Y-%m-%d")
    date_text = f"Generated on: {current_date}"
    elements.append(Paragraph(date_text, styles['Normal']))
    elements.append(Spacer(1, 20))
    
    # Sales Summary Table
    orders = user_data.get('orders', [])
    total_revenue = sum(order.get('total', 0) for order in orders)
    total_orders = len(orders)
    
    summary_data = [
        ['Sales Summary', ''],
        ['Total Revenue', f"₹{total_revenue:,.2f}"],
        ['Total Orders', str(total_orders)],
        ['Average Order Value', f"₹{(total_revenue/total_orders if total_orders > 0 else 0):,.2f}"]
    ]
    
    summary_table = Table(summary_data, colWidths=[200, 200])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f8f9fa')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#666666')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 14),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 8),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 30))
    
    # Product-wise Sales Table
    product_sales = {}
    for order in orders:
        for item in order.get('items', []):
            name = item.get('name', 'Unknown')
            quantity = item.get('quantity', 0)
            price = item.get('price', 0)
            revenue = price * quantity
            
            if name in product_sales:
                product_sales[name]['quantity'] += quantity
                product_sales[name]['revenue'] += revenue
            else:
                product_sales[name] = {'quantity': quantity, 'revenue': revenue}
    
    # Sort products by revenue
    sorted_products = sorted(product_sales.items(), key=lambda x: x[1]['revenue'], reverse=True)
    
    # Product Sales Table
    elements.append(Paragraph('Product-wise Sales', subtitle_style))
    
    product_data = [['Product Name', 'Quantity Sold', 'Revenue']]
    for product_name, data in sorted_products:
        product_data.append([
            product_name,
            str(data['quantity']),
            f"₹{data['revenue']:,.2f}"
        ])
    
    product_table = Table(product_data, colWidths=[200, 100, 100])
    product_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f8f9fa')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#666666')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 8),
    ]))
    elements.append(product_table)
    elements.append(Spacer(1, 30))
    
    # Recent Orders Table
    elements.append(Paragraph('Recent Orders', subtitle_style))
    
    # Sort orders by date
    sorted_orders = sorted(orders, key=lambda x: datetime.strptime(x.get('date', ''), "%Y-%m-%d %H:%M:%S"), reverse=True)
    recent_orders = sorted_orders[:10]  # Get last 10 orders
    
    # Prepare data for table
    table_data = [['Order ID', 'Customer', 'Items', 'Total', 'Date']]
    
    for i, order in enumerate(recent_orders, 1):
        # Format items vertically, one per line
        items_str = "\n".join([f"{item['name']} (x{item['quantity']})" for item in order['items']])
        
        table_data.append([
            f"#{i:04d}",
            order['customer'],
            items_str,
            f"₹{order['total']:,.2f}",
            order['date']
        ])
    
    # Create table with properly imported inch unit
    table = Table(table_data, colWidths=[0.7*inch, 1.5*inch, 2.5*inch, 1*inch, 1.3*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (2, 1), (2, -1), 'LEFT'),  # Left align items column
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),  # Align all content to top
        ('TOPPADDING', (0, 1), (-1, -1), 12),  # Add padding to cells
        ('BOTTOMPADDING', (0, 1), (-1, -1), 12),
    ]))
    elements.append(table)
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    
    return send_file(
        buffer,
        download_name=f'sales_report_{current_date}.pdf',
        as_attachment=True,
        mimetype='application/pdf'
    )

@app.route('/get_item/<int:item_id>', methods=['GET'])
@login_required
def get_item(item_id):
    try:
        user_id = session['user_id']
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Get item with category name
        cur.execute("""
            SELECT i.*, c.name as category_name 
            FROM inventory i
            LEFT JOIN categories c ON i.category_id = c.id
            WHERE i.id = %s AND i.user_id = %s
        """, (item_id, user_id))
        
        item = cur.fetchone()
        
        cur.close()
        conn.close()
        
        if not item:
            return jsonify({
                "success": False,
                "message": "Item not found or access denied"
            }), 404
        
        # Convert to dict for JSON serialization
        item_dict = dict(item)
        
        return jsonify({
            "success": True,
            "item": item_dict
        })
        
    except Exception as e:
        print(f"Error retrieving item: {str(e)}")
        return jsonify({
            "success": False,
            "message": "Failed to retrieve item details"
        }), 500

@app.route('/edit_item/<int:item_id>', methods=['POST'])
@login_required
def edit_item(item_id):
    try:
        user_id = session['user_id']
        
        # Get form data
        name = request.form.get('name')
        description = request.form.get('description', '')
        category_id = request.form.get('category_id')
        quantity = request.form.get('quantity', 0)
        price = request.form.get('price', 0)
        cost = request.form.get('cost', 0)
        sku = request.form.get('sku', '')
        barcode = request.form.get('barcode', '')
        min_stock = request.form.get('min_stock', 0)
        max_stock = request.form.get('max_stock', 0)
        
        # Validate required fields
        if not name:
            return jsonify({
                "success": False,
                "message": "Item name is required"
            }), 400
            
        # Convert numeric values
        if category_id and category_id.isdigit():
            category_id = int(category_id)
        else:
            category_id = None
            
        quantity = int(quantity) if quantity else 0
        price = float(price) if price else 0
        cost = float(cost) if cost else 0
        min_stock = int(min_stock) if min_stock else 0
        max_stock = int(max_stock) if max_stock else 0
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Check if item exists and belongs to user
        cur.execute("SELECT * FROM inventory WHERE id = %s AND user_id = %s", (item_id, user_id))
        item = cur.fetchone()
        
        if not item:
            cur.close()
            conn.close()
            return jsonify({
                "success": False,
                "message": "Item not found or access denied"
            }), 404
            
        # Check if another item with the same name exists (excluding this one)
        cur.execute("SELECT id FROM inventory WHERE name = %s AND user_id = %s AND id != %s", 
                   (name, user_id, item_id))
        existing = cur.fetchone()
        if existing:
            cur.close()
            conn.close()
            return jsonify({
                "success": False,
                "message": f"Another item with the name '{name}' already exists"
            }), 400
        
        # Get old quantity for history
        old_quantity = item['quantity']
        quantity_change = quantity - old_quantity
        
        # Update item
        cur.execute("""
            UPDATE inventory 
            SET name = %s, description = %s, category_id = %s, quantity = %s, 
                price = %s, cost = %s, sku = %s, barcode = %s, 
                min_stock = %s, max_stock = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND user_id = %s
            RETURNING id
        """, (name, description, category_id, quantity, price, cost, sku, barcode, 
              min_stock, max_stock, item_id, user_id))
        
        # Add to history if quantity changed
        if quantity_change != 0:
            action = 'Stock Increased' if quantity_change > 0 else 'Stock Decreased'
            cur.execute(
                "INSERT INTO history (user_id, action, item, details, quantity) VALUES (%s, %s, %s, %s, %s)",
                (user_id, action, name, f"{action} for {name}", abs(quantity_change))
            )
        
        # Add general edit to history
        cur.execute(
            "INSERT INTO history (user_id, action, item, details) VALUES (%s, %s, %s, %s)",
            (user_id, 'Item Updated', name, f"Updated item details for {name}")
        )
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": f"Item '{name}' has been updated successfully"
        })
        
    except Exception as e:
        print(f"Error updating item: {str(e)}")
        return jsonify({
            "success": False,
            "message": "An error occurred while updating the item"
        }), 500

@app.route('/delete_item/<int:item_id>', methods=['POST'])
@login_required
def delete_item(item_id):
    try:
        user_id = session['user_id']
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Check if item exists and belongs to user
        cur.execute("SELECT name FROM inventory WHERE id = %s AND user_id = %s", (item_id, user_id))
        item = cur.fetchone()
        
        if not item:
            cur.close()
            conn.close()
            return jsonify({
                "success": False,
                "message": "Item not found or access denied"
            }), 404
            
        item_name = item['name']
        
        # Check if item is used in any orders
        cur.execute("SELECT id FROM order_items WHERE item_id = %s LIMIT 1", (item_id,))
        used_in_order = cur.fetchone()
        
        if used_in_order:
            # Instead of preventing deletion, just set item_id to NULL in order_items
            cur.execute("UPDATE order_items SET item_id = NULL WHERE item_id = %s", (item_id,))
        
        # Delete the item
        cur.execute("DELETE FROM inventory WHERE id = %s AND user_id = %s", (item_id, user_id))
        
        # Add to history
        cur.execute(
            "INSERT INTO history (user_id, action, item, details) VALUES (%s, %s, %s, %s)",
            (user_id, 'Item Deleted', item_name, f"Deleted item: {item_name}")
        )
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": f"Item '{item_name}' has been deleted successfully"
        })
        
    except Exception as e:
        print(f"Error deleting item: {str(e)}")
        return jsonify({
            "success": False,
            "message": "An error occurred while deleting the item"
        }), 500

@app.route('/stream')
@login_required
def stream():
    def generate():
        yield "data: {\"message\": \"Connected to event stream\"}\n\n"
        # In a real application, you would have more complex event generation
    
    response = app.response_class(
        generate(),
        mimetype='text/event-stream'
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response

@app.route('/add_item', methods=['POST'])
@login_required
def add_item():
    try:
        user_id = session['user_id']
        name = request.form.get('name')
        
        # Handle the special case when "Add New Item" is selected
        if name == 'new':
            name = request.form.get('newItemName')
        
        # Get other form data
        category_id = request.form.get('category_id')
        category = request.form.get('category')
        quantity = request.form.get('quantity')
        price = request.form.get('price')
        
        # Check if we need to create a new category
        if category and not category_id:
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            # Check if category already exists
            cur.execute("SELECT id FROM categories WHERE name = %s AND user_id = %s", 
                       (category, user_id))
            existing_category = cur.fetchone()
            
            if existing_category:
                category_id = existing_category['id']
            else:
                # Create new category
                cur.execute(
                    "INSERT INTO categories (name, user_id) VALUES (%s, %s) RETURNING id", 
                    (category, user_id)
                )
                category_id = cur.fetchone()['id']
                conn.commit()
            
            cur.close()
            conn.close()
        
        # Now insert the inventory item
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO inventory 
            (name, category_id, quantity, price, user_id) 
            VALUES (%s, %s, %s, %s, %s)
            """,
            (name, category_id, quantity, price, user_id)
        )
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "Item added successfully"
        })
        
    except Exception as e:
        print(f"Error adding item: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        })

@app.route('/get_inventory_items')
@login_required
def get_inventory_items():
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        user_id = session['user_id']
        
        # Get inventory items with their categories
        cur.execute("""
            SELECT i.id, i.name, i.quantity, i.price, 
                   c.name as category_name
            FROM inventory i
            LEFT JOIN categories c ON i.category_id = c.id
            WHERE i.user_id = %s AND i.quantity > 0
            ORDER BY i.name ASC
        """, (user_id,))
        
        items = cur.fetchall()
        
        # Convert DictRow objects to regular dictionaries
        items_list = []
        for item in items:
            items_list.append({
                'id': item['id'],
                'name': item['name'],
                'quantity': item['quantity'],
                'price': item['price'],
                'category_name': item['category_name'] or 'Uncategorized'
            })
        
        cur.close()
        conn.close()
        
        return jsonify({'success': True, 'items': items_list})
    
    except Exception as e:
        print(f"Error fetching inventory items: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(debug=os.getenv('DEBUG', 'True') == 'True', 
            host=os.getenv('HOST', '0.0.0.0'),
            port=int(os.getenv('PORT', 5000)))

