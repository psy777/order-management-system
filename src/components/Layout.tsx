import React, { useState, useRef, useEffect } from 'react';
import Link from 'next/link';
import { MenuIcon } from './ui';
import { SignInButton, SignUpButton, SignedIn, SignedOut, UserButton } from '@clerk/nextjs';
import { EmailModal } from './features';
import { OrderFormData } from './views';

interface LayoutProps {
    children: React.ReactNode;
    appSettings: { company_name: string; default_email_body: string };
    orderForEmailModal: OrderFormData | null;
    allSelectableItems: any;
    handleOrderSent: (updatedOrder: OrderFormData) => void;
    setOrderForEmailModal: (order: OrderFormData | null) => void;
}

const Layout: React.FC<LayoutProps> = ({ children, appSettings, orderForEmailModal, allSelectableItems, handleOrderSent, setOrderForEmailModal }) => {
    const [showSettingsMenu, setShowSettingsMenu] = useState(false);
    const settingsMenuRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (settingsMenuRef.current && !settingsMenuRef.current.contains(event.target as Node)) {
                setShowSettingsMenu(false);
            }
        };

        document.addEventListener("mousedown", handleClickOutside);
        return () => {
            document.removeEventListener("mousedown", handleClickOutside);
        };
    }, [settingsMenuRef]);

    return (
        <div className="bg-slate-50 min-h-screen font-sans">
            {orderForEmailModal && (
                <EmailModal
                    order={orderForEmailModal}
                    allItems={allSelectableItems}
                    appSettings={appSettings}
                    onClose={() => setOrderForEmailModal(null)}
                    onOrderSent={handleOrderSent}
                />
            )}
            <header className="bg-white shadow-sm">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex justify-between items-center h-16">
                        <div className="flex items-center relative" ref={settingsMenuRef}>
                            <UserButton />
                            <button
                                onClick={() => setShowSettingsMenu(!showSettingsMenu)}
                                className="p-2 rounded-md text-slate-500 hover:text-slate-700 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-orange-500"
                                aria-label="Main menu"
                            >
                                <MenuIcon />
                            </button>
                            <Link href="/" className="text-xl font-bold text-slate-800 ml-4">{appSettings.company_name}</Link>
                            {showSettingsMenu && (
                                <div 
                                    className="absolute top-full mt-2 w-56 bg-white rounded-md shadow-xl py-1 ring-1 ring-black ring-opacity-5 focus:outline-none transition ease-out duration-100"
                                    role="menu"
                                    aria-orientation="vertical"
                                    aria-labelledby="options-menu"
                                    style={{ left: '50%', transform: 'translateX(-50%)' }}
                                >
                                    <Link href="/" className="block px-4 py-2 text-sm text-slate-700 hover:bg-orange-100 hover:text-orange-600" role="menuitem">
                                        Home
                                    </Link>
                                    <Link href="/settings" className="block px-4 py-2 text-sm text-slate-700 hover:bg-orange-100 hover:text-orange-600" role="menuitem">
                                        Settings
                                    </Link>
                                    <div className="border-t border-slate-200 my-1"></div>
                                    <Link href="/manage-customers" className="block px-4 py-2 text-sm text-slate-700 hover:bg-orange-100 hover:text-orange-600" role="menuitem">
                                        Manage Customers
                                    </Link>
                                    <Link href="/manage-items" className="block px-4 py-2 text-sm text-slate-700 hover:bg-orange-100 hover:text-orange-600" role="menuitem">
                                        Manage Items
                                    </Link>
                                    <Link href="/manage-packages" className="block px-4 py-2 text-sm text-slate-700 hover:bg-orange-100 hover:text-orange-600" role="menuitem">
                                        Manage Packages
                                    </Link>
                                </div>
                            )}
                        </div>
                        <div className="flex items-center">
                        </div>
                    </div>
                </div>
            </header>
            <div className="max-w-7xl mx-auto p-4 sm:p-6 lg:p-8">
                {children}
            </div>
        </div>
    );
};

export default Layout;
