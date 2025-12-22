# audit_slide/update_prices.py
import re
import os

# Path to the config file
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.py')

def update_config_prices():
    print("--- AuditSlide AI: Price Updater Tool ---")
    print("This tool allows you to manually batch-update AI token costs.")
    print("Current Reference (Dec 2025):")
    print(" - GPT-4o Mini: $0.15")
    print(" - Claude 3.5 Sonnet: $3.00")
    print("-" * 40)

    # In a real production environment, you might fetch this from a company URL:
    # prices = requests.get("https://internal-tools.yourcompany.com/ai-pricing.json").json()
    
    # For now, we allow manual override without opening the code:
    new_rates = {}
    models = [
        "Gemini 2.5 Flash", "Gemini 2.5 Pro", 
        "GPT-4o Mini", "GPT-4o", 
        "Claude 3.5 Haiku", "Claude 3.5 Sonnet"
    ]

    print("Enter new price per 1M Input Tokens (or press Enter to keep current):")
    
    for model in models:
        val = input(f"[{model}]: $")
        if val.strip():
            try:
                new_rates[model] = float(val)
            except ValueError:
                print("❌ Invalid number, skipping.")

    if not new_rates:
        print("No changes made.")
        return

    # Read Config
    with open(CONFIG_PATH, 'r') as f:
        content = f.read()

    # Regex Magic to find the dictionary and update it safely
    # This replaces the specific lines in the text file
    for model, price in new_rates.items():
        # Look for: "Model Name": 0.00,
        pattern = re.compile(rf'"{re.escape(model)}":\s*(\d+(\.\d+)?)')
        if pattern.search(content):
            content = pattern.sub(f'"{model}": {price}', content)
            print(f"✅ Updated {model} -> ${price}")
        else:
            print(f"⚠️ Could not find {model} in config.py")

    # Write Config
    with open(CONFIG_PATH, 'w') as f:
        f.write(content)
    
    print("\n🎉 config.py has been updated successfully!")

if __name__ == "__main__":
    update_config_prices()