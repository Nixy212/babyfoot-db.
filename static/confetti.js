// 1. Lancer les confettis
launchConfetti(duration = 4000, count = 150)
// Exemple: launchConfetti(5000, 200)

// 2. Afficher popup de victoire avec confettis
showVictoryPopup(winnerTeam, winners, finalScore)
// Exemple: showVictoryPopup('team1', ['Alice', 'Bob'], '10 - 5')

// 3. Fermer le popup
closeVictoryPopup()

// 4. Célébration d'un but marqué
celebrateGoal(team, scoreElement)
// Exemple: celebrateGoal('team1', document.getElementById('score1'))

// 5. Compte à rebours avant match
startCountdown(callback)
// Exemple: startCountdown(() => console.log('GO!'))

// 6. Afficher une notification toast
showToast(message, type = 'inf')
// Exemple: showToast('But marqué !', 'ok')
