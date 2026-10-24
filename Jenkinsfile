// NAS deploy: pull /volume3/Gullinkambi and restart Grafana only when alerting or dashboard provider yml changed.
// Dashboards need no restart (file providers rescan every 60s; Nest-Control-Room uses Git Sync).
pipeline {
    agent { label 'robingraph-nas-deploy' }
    triggers { pollSCM('H/5 * * * *') }
    options {
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
        buildDiscarder(logRotator(numToKeepStr: '20'))
        timeout(time: 10, unit: 'MINUTES')
    }
    stages {
        stage('Deploy to NAS') {
            steps {
                sh '''
                    set -eu
                    # The agent has no git; borrow it from the Jenkins image already on the NAS.
                    git() { docker run --rm --entrypoint git --user 1000:10 -e HOME=/tmp \
                        -v /volume3/Gullinkambi:/repo -w /repo jenkins/jenkins:lts-jdk21 "$@"; }
                    old=$(git rev-parse HEAD)
                    git pull --ff-only origin main
                    new=$(git rev-parse HEAD)
                    echo "Gullinkambi ${old} -> ${new}"
                    if git diff --name-only "$old" "$new" -- provisioning/alerting 'provisioning/dashboards/*.yml' | grep -q .; then
                        docker restart grafana
                    fi
                '''
            }
        }
    }
}
