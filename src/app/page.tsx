import React from 'react';
import { SignedIn, SignedOut, SignIn } from '@clerk/nextjs';
import AppPage from '../components/AppPage'; // This will be our new component

export default function HomePage() {
    return (
        <>
            <SignedIn>
                <AppPage />
            </SignedIn>
            <SignedOut>
                <div className="flex justify-center items-center h-screen">
                    <SignIn routing="hash" />
                </div>
            </SignedOut>
        </>
    );
}
