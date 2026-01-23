# POS Daily Sale Button Control

## Overview

POS Daily Sale Button Control is an Odoo 19 module that provides user-specific control over the visibility of the "Daily Sale" button in the Point of Sale (POS) closing popup footer. Each user can independently configure whether they want to see or hide this button based on their workflow preferences.

## Features

- **User-Specific Control**: Each user can independently enable or disable the Daily Sale button visibility
- **Easy Configuration**: Setting is available in the User form view under the "POS Settings" tab
- **Seamless Integration**: Works with Odoo 19 POS without modifying core functionality
- **Flexible**: Button visibility is controlled per user, allowing different preferences for different users

## Installation

1. Copy the module to your Odoo addons directory
2. Update the apps list in Odoo
3. Install the module from the Apps menu

## Configuration

1. Go to **Settings** → **Users & Companies** → **Users**
2. Open the user for whom you want to configure the Daily Sale button
3. Navigate to the **POS Settings** tab
4. Toggle the **"Hide Daily Sale Button"** option:
   - **Enabled (ON)**: The Daily Sale button will be visible in the POS closing popup
   - **Disabled (OFF)**: The Daily Sale button will be hidden in the POS closing popup

## Usage

Once configured, when a user closes a POS session:
- If the setting is **enabled**, the Daily Sale button will appear in the closing popup footer
- If the setting is **disabled**, the Daily Sale button will be hidden from the closing popup footer

## Technical Details

- **Odoo Version**: 19.0
- **Dependencies**: point_of_sale
- **License**: OPL-1
- **Price**: $10 USD

## Support

For issues, questions, or contributions, please contact the module author.

## Author

**Muhammad nadeem**  
Company: nk  
GitHub: [https://github.com/nadeemkhn](https://github.com/nadeemkhn)  
Email: nadeemwazir0123@gmail.com

## Version History

- **19.0.1.0.0**: Initial release for Odoo 19
