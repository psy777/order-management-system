export interface Settings {
  id: number;
  company_name: string;
  default_shipping_zip_code: string;
  default_email_body: string;
  email_address: string;
  email_cc: string;
  email_bcc: string;
  GMAIL_CLIENT_ID: string;
  GMAIL_CLIENT_SECRET: string;
}

export interface User {
    id: string;
    username: string;
    email: string;
    GMAIL_REFRESH_TOKEN: string;
}
